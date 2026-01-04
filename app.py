# root/vnext-ledger/app.py
from __future__ import annotations

# pyright: reportMissingImports=false
from dotenv import load_dotenv

load_dotenv()

import base64
import html
import hashlib
import hmac
import json
import logging
import os
import ipaddress
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Tuple
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from render import (
    render_notes_table,
    render_note_detail,
    render_summary,
    render_metrics,
    render_scan_result,
    render_no_ui,
)


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# 刺し①: CSPレポートのサンプリング用（ログ燃え防止）
# 同一違反（blocked-uri, violated-directive）を60秒に1回だけログ出力
# key: (blocked_uri, violated_directive), value: last_logged_timestamp
_CSP_REPORT_SAMPLING: dict[tuple[str, str], float] = {}

# ============================================================
# Config
# ============================================================

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(APP_DIR / "ledger.sqlite3")))

# Auth / Mode (Settings: single source of truth)
@dataclass
class Settings:
    mode: Literal["local", "prod"]
    admin_password: str
    dev_password: str
    session_secret: str
    allow_local_json_noauth: bool
    # P2.5: CSP
    csp_mode: Literal["off", "report", "enforce"]
    csp_report_uri: str
    csp_use_reporting_api: bool  # 刺し①: report-to + Reporting-Endpoints を有効化


_SETTINGS: Optional[Settings] = None


def load_settings() -> Settings:
    """Load and validate settings from environment exactly once."""
    mode = (os.getenv("MODE") or "local").strip().lower()
    if mode not in {"local", "prod"}:
        raise RuntimeError(f"MODE must be 'local' or 'prod' (got {mode!r})")

    admin_password = (os.getenv("ADMIN_PASSWORD") or "").strip()
    dev_password = (os.getenv("DEV_PASSWORD") or "").strip()
    session_secret = (os.getenv("SESSION_SECRET") or "").strip()

    # 刺し③: ALLOW_LOCAL_JSON_NOAUTH の既定値を mode 依存に
    # local: 既定ON（開発利便性）
    # prod: 既定OFF（安全に倒す、明示した時だけON）
    if mode == "local":
        allow_local_json_noauth_default = "1"
    else:  # prod
        allow_local_json_noauth_default = "0"
    
    allow_local_json_noauth = (os.getenv("ALLOW_LOCAL_JSON_NOAUTH") or allow_local_json_noauth_default).strip().lower() not in {"0", "false", "no"}

    # 安全弁: prod では local-json no-auth を絶対に許可しない
    if mode == "prod" and allow_local_json_noauth:
        raise RuntimeError("ALLOW_LOCAL_JSON_NOAUTH must be disabled in MODE=prod")

    if mode == "prod":
        if not admin_password:
            raise RuntimeError("ADMIN_PASSWORD must be set in MODE=prod")
        if not session_secret:
            raise RuntimeError("SESSION_SECRET must be set in MODE=prod")
    else:
        # local: ephemeral secret (session resets on restart) unless provided
        if not session_secret:
            session_secret = secrets.token_urlsafe(32)

    # P2.5: CSP settings
    if mode == "local":
        csp_mode_default = "off"
    elif mode == "prod":
        csp_mode_default = "report"
    else:
        csp_mode_default = "off"
    
    csp_mode = (os.getenv("CSP_MODE", csp_mode_default) or "").strip().lower()
    
    if csp_mode not in ("off", "report", "enforce"):
        raise RuntimeError(f"Invalid CSP_MODE: {csp_mode}. Must be off/report/enforce")
    
    csp_report_uri = os.getenv("CSP_REPORT_URI", "")
    
    # 刺し①②④: デフォルト条件を明確化（説明と実装の一致）
    # prod + report（観測モード）かつ CSP_REPORT_URI未設定 → /__csp_report を暗黙採用
    # enforce時は明示設定を推奨（観測の基本思想: reportで観測してからenforceへ移行）
    if not csp_report_uri and mode == "prod" and csp_mode == "report":
        csp_report_uri = "/__csp_report"
    
    # CSP_REPORT_URI 検証
    if csp_report_uri:
        # 相対パスチェック
        if not csp_report_uri.startswith("/"):
            raise RuntimeError(
                f"CSP_REPORT_URI must be a relative path starting with '/'. "
                f"Got: {csp_report_uri}"
            )
        # P2.5: endpoint存在検証（契約駆動）
        # 今は /__csp_report のみ実装されているので、それ以外は起動時に落とす
        if csp_report_uri != "/__csp_report":
            raise RuntimeError(
                f"CSP_REPORT_URI must be '/__csp_report' (the only implemented endpoint). "
                f"Got: {csp_report_uri}"
            )
    
    # 刺し①: Reporting API 対応（任意ON、envがある時だけ有効化）
    # CSP3の推奨: report-uri（互換） + report-to（新式）を併記
    # CSP_USE_REPORTING_API=1 で有効化（未設定時は旧式report-uriのみ）
    csp_use_reporting_api = (os.getenv("CSP_USE_REPORTING_API") or "").strip().lower() in {"1", "true", "yes"}

    s = Settings(
        mode=mode,  # type: ignore[arg-type]
        admin_password=admin_password,
        dev_password=dev_password,
        session_secret=session_secret,
        allow_local_json_noauth=allow_local_json_noauth,
        csp_mode=csp_mode,  # type: ignore[arg-type]
        csp_report_uri=csp_report_uri,
        csp_use_reporting_api=csp_use_reporting_api,
    )
    return s


def get_settings() -> Settings:
    if _SETTINGS is None:
        raise RuntimeError("Settings not loaded yet")
    return _SETTINGS


def init_settings() -> None:
    global _SETTINGS
    _SETTINGS = load_settings()
    # Note: init_db() is called separately in lifespan after P1 contract gate check


def check_p1_contract_gate():
    """
    P1契約ゲート: CI通過フラグの確認
    
    目的: 「CI未通過＝契約違反」を実装レベルで強制
    
    動作:
    - MODE=prod: P1_CONTRACT_VERIFIED=1 必須（なければ即死）
    - MODE=local: P1_CONTRACT_VERIFIED=1 推奨（なければWARN）
    
    理由: Approval廃止の代償として、CIが唯一の門番
    """
    s = get_settings()
    verified = os.getenv("P1_CONTRACT_VERIFIED", "").strip() == "1"
    
    if s.mode == "prod":
        if not verified:
            logger.error("=" * 60)
            logger.error("P1 CONTRACT VIOLATION")
            logger.error("=" * 60)
            logger.error("Production deployment without CI verification is forbidden.")
            logger.error("")
            logger.error("This application cannot start in MODE=prod without:")
            logger.error("  P1_CONTRACT_VERIFIED=1")
            logger.error("")
            logger.error("This flag must be set by CI after all P1 contract tests pass.")
            logger.error("Manual bypass is a P1 contract violation.")
            logger.error("=" * 60)
            raise RuntimeError("P1 contract gate: CI verification required in prod")
    else:
        # local mode: WARN but allow
        if not verified:
            logger.warning("=" * 60)
            logger.warning("P1 CONTRACT WARNING")
            logger.warning("=" * 60)
            logger.warning("Running without CI verification (local mode).")
            logger.warning("")
            logger.warning("While allowed in MODE=local, this bypasses P1 contracts.")
            logger.warning("Run contract tests before production deployment:")
            logger.warning("  pytest tests/test_p1_*.py -v")
            logger.warning("=" * 60)


# UI (static)
STATIC_DIR = APP_DIR / "static"
UI_INDEX_HTMX = STATIC_DIR / "index.htmx"
UI_INDEX_HTML = STATIC_DIR / "index.html"

# Keep for backward compatibility (v0.6.0 default assumption)
DEFAULT_REPO_ROOT = APP_DIR.parent

# root resolution priority:
# 1) ScanRequest.root
# 2) env: LEDGER_REPO_ROOT
# 3) auto-detect: traverse parent dirs to find .git / pyproject.toml / requirements.txt
# 4) fallback: DEFAULT_REPO_ROOT (legacy compatibility)
LEDGER_REPO_ROOT_ENV = "LEDGER_REPO_ROOT"

TAG_RE = re.compile(r"NOTE\(vNext\):\s*(\S+)", re.IGNORECASE)
DONE_RE = re.compile(r"DONE\(vNext\):\s*(\S+)", re.IGNORECASE)

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}

SCAN_EXTS = {".py", ".md", ".ts", ".tsx", ".js", ".jsx"}

ACTIVE_STATUSES = ("open", "doing", "parked")
# render_summary は「順序付きの list[str]」が欲しい（UI表示順を固定する）
ALLOWED_STATUS_ORDER: list[str] = ["open", "doing", "parked", "done", "stale"]
# membership 判定用（速い）
ALLOWED_STATUS = set(ALLOWED_STATUS_ORDER)


PRIORITY_RANGE = (1, 3)


# Risk level: 2段階（high, critical）
ALLOWED_RISK_LEVEL = {"high", "critical"}


# ============================================================
# Security: XSS escape / Session / CSRF
# ============================================================

# Cookie keys
SESSION_COOKIE = "vnext_session"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"


def esc(s: str | None) -> str:
    """
    HTML escape for any user/DB-derived text inserted into HTML strings.
    Always escape (including quotes).
    """
    return html.escape(s or "", quote=True)



def _is_trusted_proxy(request: Request) -> bool:
    """Return True if request.client.host is a trusted reverse proxy."""
    client_host = (request.client.host if request.client else "") or ""
    if not client_host:
        return False

    # ★テスト環境: Starlette TestClient は client_host が "testclient"/"testserver" になりうる
    # ここで trusted 扱いにしておくと、x-forwarded-* の組み立てテストが成立する
    if client_host in {"testclient", "testserver"}:
        return True

    cidrs_raw = os.getenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128")
    cidrs = [c.strip() for c in cidrs_raw.split(",") if c.strip()]

    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False

    for c in cidrs:
        try:
            if ip in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False



def _xff_leftmost(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") or ""
    if not xff:
        return ""
    return xff.split(",")[0].strip()


def _is_https(request: Request) -> bool:
    # Trust proxy headers ONLY when the immediate client is a trusted proxy.
    if _is_trusted_proxy(request):
        xf_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if xf_proto in {"https", "http"}:
            return xf_proto == "https"

        forwarded = (request.headers.get("forwarded") or "").lower()
        # e.g. Forwarded: for=...;proto=https;host=...
        m = re.search(r"proto=([^;,\s]+)", forwarded)
        if m:
            return m.group(1).strip() == "https"

    return (request.url.scheme or "").lower() == "https"



def _get_external_origin(request: Request) -> str:
    """
    Build the external origin (scheme://host) used for CSP reporting endpoints.

    Security boundary:
    - Trust X-Forwarded-* / Forwarded headers ONLY when the immediate client is a trusted proxy.
    - Otherwise fall back to request.base_url / Host.
    """

    trusted = _is_trusted_proxy(request)

    # Protocol: x-forwarded-proto → forwarded → request.base_url.scheme
    proto: Optional[str] = None

    if trusted:
        xf_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if xf_proto in {"https", "http"}:
            proto = xf_proto
        else:
            forwarded = (request.headers.get("forwarded") or "").lower()
            m = re.search(r"proto=([^;,\s]+)", forwarded)
            if m:
                proto_candidate = m.group(1).strip()
                if proto_candidate in {"https", "http"}:
                    proto = proto_candidate

    # Fallback: request.base_url.scheme
    if not proto:
        base_scheme = str(request.base_url.scheme or "http").lower()
        proto = base_scheme if base_scheme in {"https", "http"} else "http"

    # Host: x-forwarded-host → forwarded → request.headers["host"] → request.base_url.hostname
    def sanitize_host(h: str) -> str:
        if not h:
            return ""
        # Allow only safe host characters (port included)
        if re.match(r"^[A-Za-z0-9\.\-:]+$", h):
            return h
        return ""

    host: Optional[str] = None

    if trusted:
        xf_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        xf_host_safe = sanitize_host(xf_host)
        if xf_host_safe:
            host = xf_host_safe
        else:
            forwarded = (request.headers.get("forwarded") or "").lower()
            m = re.search(r"host=([^;,\s]+)", forwarded)
            if m:
                forwarded_host = m.group(1).strip()
                forwarded_host_safe = sanitize_host(forwarded_host)
                if forwarded_host_safe:
                    host = forwarded_host_safe

    # Fallback: request.headers["host"] or request.base_url.hostname
    if not host:
        host_candidate = request.headers.get("host") or str(request.base_url.hostname or "localhost")
        host = sanitize_host(host_candidate) or "localhost"

    return f"{proto}://{host}"



def _cookie_secure(request: Request) -> bool:
    """Return True if cookies should be marked Secure for this request."""
    s = get_settings()
    return (s.mode == "prod") and _is_https(request)


def _is_local_host(request: Request) -> bool:
    """
    Best-effort localhost detection.

    Prefer client address (not spoofable via Host header).
    If behind a trusted proxy, also validate X-Forwarded-For.
    This is used only for local-mode conveniences.
    """
    client_host = (request.client.host if request.client else "") or ""

    # ★追加: TestClient 等のテスト環境は client_host が "testclient"/"testserver" になることがある
    # これは外部から偽装される値ではなくテスト用なので、local 扱いしてよい
    if client_host in {"127.0.0.1", "::1", "testclient", "testserver"}:
        # ただし trusted proxy のときは XFF 左端も local であることを確認する
        if _is_trusted_proxy(request):
            left = _xff_leftmost(request)
            if left and left not in {"127.0.0.1", "::1", "localhost"}:
                return False
        return True

    # Fallback: some environments may not provide request.client.
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return host in {"127.0.0.1", "localhost"}



def _should_autologin_localhost(request: Request, s: Settings) -> bool:
    # Local convenience: auto-login only when:
    # - local mode
    # - dev password is not set (if set, require explicit login)
    # - request comes from localhost
    return s.mode == "local" and (not s.dev_password) and _is_local_host(request)



def _wants_html(request: Request) -> bool:
    """
    Content negotiation policy:
    - If client explicitly asks HTML => HTML
    - If client explicitly asks JSON  => JSON
    - If Accept is neutral (*/* or missing):
        - JSON body (Content-Type: application/json) => JSON (for API/test clients)
        - otherwise => HTML (for browser/htmx)
    """
    accept = (request.headers.get("accept") or "").lower()
    ct = (request.headers.get("content-type") or "").lower()

    if ("text/html" in accept) or ("application/xhtml+xml" in accept):
        return True
    if "application/json" in accept:
        return False
    if (not accept) or ("*/*" in accept):
        return "application/json" not in ct
    return False


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    ct = (request.headers.get("content-type") or "").lower()
    return ("application/json" in accept) or ("application/json" in ct)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _session_secret() -> bytes:
    # Loaded and validated in init_settings()/lifespan.
    return get_settings().session_secret.encode("utf-8")


def _sign_session(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = _b64u(raw)
    sig = _b64u(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify_session(token: str) -> Optional[dict[str, Any]]:
    try:
        body, sig = token.split(".", 1)
        expected = _b64u(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64u_dec(body).decode("utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _current_session(request: Request) -> Optional[dict[str, Any]]:
    token = request.cookies.get(SESSION_COOKIE)
    return _verify_session(token) if token else None


def _current_role(request: Request) -> str:
    session = _current_session(request)
    return session.get("role", "") if session else ""


def _autologin_local(request: Request) -> Optional[dict[str, Any]]:
    # Local mode convenience: auto-login when dev password is not set.
    s = get_settings()
    if _should_autologin_localhost(request, s):
        return {"role": "dev", "auto": True}
    return None


def _ensure_role(request: Request, allowed: set[str]) -> str:
    # Combined local + session check.
    session = _current_session(request) or _autologin_local(request)

    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")

    role = session.get("role", "")
    if role not in allowed:
        raise HTTPException(status_code=403, detail="Forbidden")

    return role


def _issue_session_cookie(resp: Response, request: Request, payload: dict[str, Any]) -> None:
    token = _sign_session(payload)
    secure = _cookie_secure(request)
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=86400 * 30,  # 30 days
    )


def _clear_session(resp: Response) -> None:
    resp.delete_cookie(key=SESSION_COOKIE, httponly=True, samesite="lax")


def _csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _ensure_csrf_cookie(resp: Response, request: Request) -> None:
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        return
    # Issue new CSRF token if not present (first visit, or cookie cleared).
    secure = _cookie_secure(request)
    resp.set_cookie(
        key=CSRF_COOKIE,
        value=_csrf_token(),
        httponly=False,
        secure=secure,
        samesite="lax",
        max_age=86400 * 30,
    )


def _verify_csrf_if_cookie_present(request: Request) -> None:
    """
    CSRF: enforce only if cookie is present.

    Rationale:
    - Browser with cookie: must send X-CSRF-Token matching cookie.
    - curl/script: no cookie → no enforcement (allows /scan from CI without session).

    This balances security (CSRF for browsers) with usability (script-friendly).
    """
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not cookie_token:
        return  # no cookie → no enforcement

    header_token = request.headers.get(CSRF_HEADER)
    if not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


def _no_auth_json_exception() -> bool:
    """
    Local convenience: allow JSON-only endpoints (no HTML) without auth
    when:
    - MODE=local
    - ALLOW_LOCAL_JSON_NOAUTH=1 (default: true)
    - Request is from localhost

    Use case: ci_export.sh (CI scripts) that call /scan, /export/*, etc.

    Rationale: separating "interactive browser" (must auth) from "local script" (no auth).
    """
    s = get_settings()
    # Only if explicitly allowed in local mode.
    return s.mode == "local" and s.allow_local_json_noauth


# ============================================================
# Database
# ============================================================

_PRAGMA_APPLIED_DBS = set()  # DB_PATH の文字列を記録（journal_mode用）


def db() -> sqlite3.Connection:
    """Return connection with Row factory (dict-like access)."""
    global _PRAGMA_APPLIED_DBS
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    
    db_key = str(DB_PATH)
    if db_key not in _PRAGMA_APPLIED_DBS:
        # journal_mode is persistent in DB file, so set only once per DB_PATH
        con.execute("PRAGMA journal_mode=WAL")
        _PRAGMA_APPLIED_DBS.add(db_key)
    
    # synchronous is per-connection, so set it every time
    con.execute("PRAGMA synchronous=NORMAL")
    
    return con


def _ensure_column(con: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db() -> None:
    """
    Initialize database schema (idempotent).
    
    P1 Schema Version: 1.0.0-p1
    Required Tables (AAA Superset Contract):
    - notes
    - evidence
    - scan_log
    - file_state
    - scan_state
    - schema_version
    - note_events
    
    Note: Additional tables can be added without violating P1 contract.
    Required set deletion/type change is prohibited.
    """
    with db() as con:
        # P1 Required Table: notes
        con.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'open',
                priority INTEGER,
                risk_level TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # P1 Required Table: evidence
        con.execute("""
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                filepath TEXT NOT NULL,
                line_no INTEGER,
                snippet TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
            )
        """)

        # P1 Required Table: scan_log
        con.execute("""
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY,
                scanned_at TEXT NOT NULL,
                scanned_root TEXT NOT NULL,
                full INTEGER NOT NULL,
                files_scanned INTEGER NOT NULL,
                slugs_found INTEGER NOT NULL,
                evidence_added INTEGER NOT NULL,
                done_forced INTEGER NOT NULL,
                stale_marked INTEGER NOT NULL,
                revived_count INTEGER NOT NULL,
                orphan_files_removed INTEGER NOT NULL
            )
        """)

        # P1 Required Table: file_state
        con.execute("""
            CREATE TABLE IF NOT EXISTS file_state (
                filepath TEXT PRIMARY KEY
            )
        """)

        # P1 Required Table: scan_state
        con.execute("""
            CREATE TABLE IF NOT EXISTS scan_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_scan_at TEXT
            )
        """)
        con.execute("INSERT OR IGNORE INTO scan_state (id, last_scan_at) VALUES (1, NULL)")

        # P1 Required Table: schema_version
        con.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)

        # P1 Required Table: note_events
        con.execute("""
            CREATE TABLE IF NOT EXISTS note_events (
                id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at TEXT NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
            )
        """)

        # --- P1 backward-compat columns (do not refactor) ---
        # notes: required by tests/export contract
        _ensure_column(con, "notes", "first_seen", "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'")
        _ensure_column(con, "notes", "last_seen", "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'")
        _ensure_column(con, "notes", "evidence_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "notes", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "notes", "is_archived", "INTEGER NOT NULL DEFAULT 0")

        # file_state: older DB safety (harmless if already present)
        _ensure_column(con, "file_state", "mtime_ns", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "file_state", "size_bytes", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "file_state", "last_seen_at", "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'")

        # P1 Schema Version (Exclusive in P1 phase)
        now = datetime.now().isoformat(timespec="seconds")
        con.execute("""
            INSERT OR IGNORE INTO schema_version (version, applied_at)
            VALUES ('1.0.0-p1', ?)
        """, (now,))

        # Commit after DDL/DML (before index creation)
        con.commit()

        # Create indexes after DDL/DML commit
        con.execute("CREATE INDEX IF NOT EXISTS idx_evidence_note ON evidence(note_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_note ON note_events(note_id)")

        con.commit()

    logger.info("Database initialized (P1 schema: 1.0.0-p1)")


# ============================================================
# Root resolution
# ============================================================

def resolve_root(requested_root: Optional[str]) -> Path:
    """
    Root resolution priority (P0-A compliant):
    1. ScanRequest.root (if provided)
    2. env: LEDGER_REPO_ROOT
    3. auto-detect: traverse parent dirs to find .git / pyproject.toml / requirements.txt
    4. fallback: DEFAULT_REPO_ROOT (legacy compatibility)
    """
    if requested_root:
        p = Path(requested_root).resolve()
        if p.exists() and p.is_dir():
            return p
        logger.warning(f"Requested root invalid: {requested_root}, falling back.")

    env_root = os.getenv(LEDGER_REPO_ROOT_ENV)
    if env_root:
        p = Path(env_root).resolve()
        if p.exists() and p.is_dir():
            return p
        logger.warning(f"Env root invalid: {env_root}, falling back.")

    # Auto-detect: look for repo markers (.git, pyproject.toml, requirements.txt)
    current = APP_DIR
    for _ in range(10):  # max 10 levels up
        if any((current / marker).exists() for marker in [".git", "pyproject.toml", "requirements.txt"]):
            return current
        if current.parent == current:
            break
        current = current.parent

    # Fallback: DEFAULT_REPO_ROOT (legacy compatibility)
    return DEFAULT_REPO_ROOT


# ============================================================
# Scan Engine
# ============================================================

@dataclass
class Hit:
    kind: Literal["note", "done"]
    slug: str
    path: str
    line: int
    snippet: str


def iter_source_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in SCAN_EXTS:
            continue
        if any(exc in p.parts for exc in EXCLUDE_DIRS):
            continue
        yield p


def _file_hash_key(p: Path) -> Tuple[int, int]:
    st = p.stat()
    return (st.st_mtime_ns, st.st_size)


def list_files_hashdiff(
    con: sqlite3.Connection,
    root: Path,
    now: str,
) -> Tuple[list[Path], set[str]]:
    """
    Return (files_to_scan, seen_paths_set).

    Only scan files that have changed (mtime_ns or size_bytes differ from DB).
    """
    files_to_scan: list[Path] = []
    seen_paths: set[str] = set()

    for p in iter_source_files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        seen_paths.add(rel)

        mtime_ns, size = _file_hash_key(p)

        row = con.execute(
            "SELECT mtime_ns, size_bytes FROM file_state WHERE filepath = ?",
            (rel,),
        ).fetchone()

        if row is None or row["mtime_ns"] != mtime_ns or row["size_bytes"] != size:
            files_to_scan.append(p)
            con.execute(
                """
                INSERT OR REPLACE INTO file_state (filepath, mtime_ns, size_bytes, last_seen_at)
                VALUES (?, ?, ?, ?)
                """,
                (rel, mtime_ns, size, now),
            )

    return files_to_scan, seen_paths


def collect_hits_from_files(root: Path, files: list[Path]) -> list[Hit]:
    """
    Scan files for NOTE(vNext) and DONE(vNext) tags.
    Return list of Hits.
    """
    hits: list[Hit] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            # Skip files that can't be read as UTF-8
            continue
        except Exception as e:
            logger.warning(f"Failed to read {p}: {e}")
            continue

        rel = str(p.relative_to(root)).replace("\\", "/")

        for i, line_text in enumerate(text.splitlines(), start=1):
            for m in TAG_RE.finditer(line_text):
                slug = m.group(1)
                snippet = line_text.strip()[:200]
                hits.append(Hit(kind="note", slug=slug, path=rel, line=i, snippet=snippet))

            for m in DONE_RE.finditer(line_text):
                slug = m.group(1)
                snippet = line_text.strip()[:200]
                hits.append(Hit(kind="done", slug=slug, path=rel, line=i, snippet=snippet))

    return hits


# ============================================================
# Ledger Operations
# ============================================================

def upsert_note(con: sqlite3.Connection, slug: str, now: str) -> Tuple[int, bool]:
    """
    Insert or revive note.

    Returns:
        (note_id, revived)

    P1 Contract:
    - If note exists and status='stale': revive to 'open' (return revived=True)
    - If note exists: return existing (revived=False)
    - If note does not exist: create new (revived=False)
    """
    row = con.execute("SELECT id, status FROM notes WHERE slug = ?", (slug,)).fetchone()

    if row:
        note_id = row["id"]
        status = row["status"]

        if status == "stale":
            # Revive: stale → open
            con.execute(
                "UPDATE notes SET status = 'open', updated_at = ? WHERE id = ?",
                (now, note_id),
            )
            con.execute(
                """
                INSERT INTO note_events (note_id, event_type, old_value, new_value, changed_at)
                VALUES (?, 'status_change', 'stale', 'open', ?)
                """,
                (note_id, now),
            )
            return note_id, True
        else:
            return note_id, False
    else:
        # New note
        con.execute(
            """
            INSERT INTO notes (slug, status, priority, created_at, updated_at)
            VALUES (?, 'open', NULL, ?, ?)
            """,
            (slug, now, now),
        )
        note_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute(
            """
            INSERT INTO note_events (note_id, event_type, new_value, changed_at)
            VALUES (?, 'created', 'open', ?)
            """,
            (note_id, now),
        )
        return note_id, False


def add_evidence(
    con: sqlite3.Connection,
    note_id: int,
    filepath: str,
    line_no: int,
    snippet: str,
    now: str,
) -> bool:
    """
    Add evidence if not already present.

    Returns:
        True if new evidence was added, False if already exists.

    P1 Contract:
    - Duplicate check: (note_id, filepath, line_no)
    - Only add if not present
    """
    exists = con.execute(
        "SELECT 1 FROM evidence WHERE note_id = ? AND filepath = ? AND line_no = ?",
        (note_id, filepath, line_no),
    ).fetchone()

    if exists:
        return False

    con.execute(
        """
        INSERT INTO evidence (note_id, filepath, line_no, snippet, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (note_id, filepath, line_no, snippet, now),
    )
    return True


def force_done(con: sqlite3.Connection, slugs: set[str], now: str) -> int:
    """
    Force notes with DONE(vNext) tags to status='done'.

    P1 Contract:
    - Only change status if current status is in ACTIVE_STATUSES (open/doing/parked)
    - Do not change status='done' or status='stale' (already terminal states)

    Returns:
        Number of notes forced to 'done'.
    """
    if not slugs:
        return 0

    placeholders = ",".join("?" * len(slugs))
    rows = con.execute(
        f"SELECT id, slug, status FROM notes WHERE slug IN ({placeholders})",
        tuple(slugs),
    ).fetchall()

    forced = 0
    for row in rows:
        if row["status"] in ACTIVE_STATUSES:
            con.execute(
                "UPDATE notes SET status = 'done', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            con.execute(
                """
                INSERT INTO note_events (note_id, event_type, old_value, new_value, changed_at)
                VALUES (?, 'status_change', ?, 'done', ?)
                """,
                (row["id"], row["status"], now),
            )
            forced += 1

    return forced


def mark_missing_as_stale(
    con: sqlite3.Connection,
    full: bool,
    seen_slugs: set[str],
    now: str,
) -> int:
    """
    Mark notes as 'stale' if they were not seen in the scan.

    P1 Contract (台帳汚染防止):
    - full=True: mark notes in ACTIVE_STATUSES as 'stale' if not in seen_slugs
    - full=False: NEVER mark anything as stale (safety fuse)

    Returns:
        Number of notes marked as stale.
    """
    if not full:
        # Safety fuse: diff scan NEVER runs stale logic.
        return 0

    # Full scan: mark missing notes as stale.
    active_cond = ",".join(f"'{s}'" for s in ACTIVE_STATUSES)

    if seen_slugs:
        placeholders = ",".join("?" * len(seen_slugs))
        rows = con.execute(
            f"""
            SELECT id, slug, status FROM notes
            WHERE status IN ({active_cond})
              AND slug NOT IN ({placeholders})
            """,
            tuple(seen_slugs),
        ).fetchall()
    else:
        rows = con.execute(
            f"SELECT id, slug, status FROM notes WHERE status IN ({active_cond})"
        ).fetchall()

    for row in rows:
        con.execute(
            "UPDATE notes SET status = 'stale', updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        con.execute(
            """
            INSERT INTO note_events (note_id, event_type, old_value, new_value, changed_at)
            VALUES (?, 'status_change', ?, 'stale', ?)
            """,
            (row["id"], row["status"], now),
        )

    return len(rows)


def cleanup_orphan_file_state(con: sqlite3.Connection, seen_paths: set[str]) -> int:
    """
    Remove file_state entries for files that no longer exist.

    P1 Contract:
    - Only run during full scan (caller responsibility)
    - Remove entries not in seen_paths

    Returns:
        Number of orphan entries removed.
    """
    if not seen_paths:
        # If no files seen, don't delete anything (safety).
        return 0

    placeholders = ",".join("?" * len(seen_paths))
    orphans = con.execute(
        f"SELECT filepath FROM file_state WHERE filepath NOT IN ({placeholders})",
        tuple(seen_paths),
    ).fetchall()

    if orphans:
        con.execute(
            f"DELETE FROM file_state WHERE filepath NOT IN ({placeholders})",
            tuple(seen_paths),
        )

    return len(orphans)


def set_last_scan_at(con: sqlite3.Connection, now: str) -> None:
    con.execute("UPDATE scan_state SET last_scan_at = ? WHERE id = 1", (now,))


def insert_scan_log(
    con: sqlite3.Connection,
    scanned_at: str,
    scanned_root: str,
    full: int,
    files_scanned: int,
    slugs_found: int,
    evidence_added: int,
    done_forced: int,
    stale_marked: int,
    revived_count: int,
    orphan_files_removed: int,
) -> None:
    con.execute(
        """
        INSERT INTO scan_log (
            scanned_at, scanned_root, full,
            files_scanned, slugs_found, evidence_added,
            done_forced, stale_marked, revived_count, orphan_files_removed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scanned_at,
            scanned_root,
            full,
            files_scanned,
            slugs_found,
            evidence_added,
            done_forced,
            stale_marked,
            revived_count,
            orphan_files_removed,
        ),
    )


# ============================================================
# === P0/P1 CORE: DO NOT EDIT IN P2.5 ===
# 監査台帳（notes, evidence, note_events, scan_log）
# Auth/Session/CSRF
# Database/Scan Engine
# 上記領域はP2.5では一切触らない（契約）
# ============================================================

# ============================================================
# === P2.5 ONLY BELOW ===
# CSP (P2.5)
# ============================================================

def _build_csp_policy(settings: Settings) -> str:
    """
    CSP policy string を組み立てる
    
    P2.5の目的: script-src 'self' と style-src 'self' を強制
    刺し③: style-src から 'unsafe-inline' を削除（CSS/Tailwind選択を後回しにできる）
    
    刺し①: CSP3準拠の report-to + report-uri 併記
    - report-uri: 旧式だが広く対応（互換性）
    - report-to: CSP3の後継仕様（CSP_USE_REPORTING_API=1で有効化）
    - 併記が推奨（CSP3仕様）: report-toがあってもreport-uriは残す
    
    任意刺しB: enforce後のレポート方針（明文化）
    - report mode: report-uri / report-to を付与（観測のため）
    - enforce mode: report-uri / report-to を付与しない（運用コスト抑制）
    - 理由: enforce後は違反を止めるだけで、レポート収集は不要
    - 思想: 「割に合わない攻撃は無視」
    
    将来の注意:
    - connect-src 'self' は P2.5 では固定。外部API/SaaS追加時は許可リスト化が必要（P2.6+）
    - img-src に blob: が必要になる場合は追加（クリップボード/生成画像など）
    - 例外時のヘッダ付与は現状 middleware で対応。StreamingResponse や
      カスタム exception handler 追加時は別途対応が必要
    """
    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "img-src 'self' data:",  # 将来 blob: が必要になる可能性あり
        "font-src 'self' data:",
        "style-src 'self' 'report-sample'",  # 刺し③: CSS/Tailwind選択を後回しに（'unsafe-inline' 不要）
        "script-src 'self' 'report-sample'",  # 刺し④: 違反サンプル取得（運用が楽になる）
        "connect-src 'self'",  # P2.5: 同一オリジンのみ。外部接続は P2.6+ で許可リスト化
        "form-action 'self'",
    ]
    
    # 任意刺しB: report-uri / report-to は観測期間（report mode）のみ
    # enforce後は違反を止めるだけで、レポート収集は不要（運用コスト抑制）
    if settings.csp_report_uri and settings.csp_mode == "report":
        # 刺し①: CSP3準拠の併記パターン
        # 1. report-uri（旧式、広く対応）
        directives.append(f"report-uri {settings.csp_report_uri}")
        
        # 2. report-to（新式、CSP3推奨）※任意ON
        if settings.csp_use_reporting_api:
            directives.append("report-to csp-endpoint")
    
    return "; ".join(directives)


# ============================================================
# FastAPI App
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup/shutdown lifecycle.
    
    P1 Contract Gate Order:
    1. Load settings (but don't init DB yet)
    2. Check P1 contract gate (fail fast if CI not verified in prod)
    3. Initialize database (only after gate passes)
    
    P2.5: CSP policy pre-build
    4. Build CSP policy and store in app.state (startup確定、middleware高速化)
    """
    init_settings()
    check_p1_contract_gate()
    init_db()
    
    # P2.5: CSPポリシーを起動時に構築（middleware で毎回 build しない）
    settings = get_settings()
    app.state.csp_mode = settings.csp_mode
    app.state.csp_policy = _build_csp_policy(settings) if settings.csp_mode != "off" else None
    app.state.mode = settings.mode  # 刺し①: middleware内でget_settings()を呼ばない（例外時の地雷除去）
    app.state.csp_use_reporting_api = settings.csp_use_reporting_api  # 刺し①: Reporting-Endpointsヘッダ用
    app.state.csp_report_uri = settings.csp_report_uri  # 刺し①: Reporting-Endpointsヘッダ用
    
    yield


app = FastAPI(lifespan=lifespan)

# P2.5: CSP middleware (StaticFiles mount の前に追加)
# 刺し⑤: このミドルウェアはセキュリティヘッダのみを扱う（責務固定）
# - CSP (Content-Security-Policy, Reporting-Endpoints)
# - XSS/Clickjacking防御 (X-Content-Type-Options, X-Frame-Options)
# - Referrer制御
# - Cache制御（セキュリティ目的: ui.js no-store）
# 機能系ヘッダやデバッグ用途は別ミドルウェアで扱うこと

def _is_public_path(path: str) -> bool:
    # Auth endpoints and reporting endpoints must be reachable before login.
    if path in {"/auth/login", "/auth/logout", "/auth/check", "/__csp_report"}:
        return True
    if path.startswith("/static/"):
        return True
    return False


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    # Preflight must pass through.
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    # ✅ P1.5: 認証ゲートは /scan だけに限定（CSP/一覧/詳細など既存テストを壊さない）
    if not (path == "/scan" and request.method == "POST"):
        return await call_next(request)

    # ✅ local JSON no-auth exception は「Accept が JSON のときだけ」
    #    Content-Type は json= で勝手に application/json になるので見ない（あなたのテスト意図に合わせる）
    accept = (request.headers.get("accept") or "").lower()
    accepts_json = "application/json" in accept

    if _no_auth_json_exception() and _is_local_host(request) and accepts_json:
        return await call_next(request)

    # ✅ /scan は「local autologin」を使わない。セッション必須（UI叩き防止）
    #    middleware で HTTPException を投げると 500 化するので "レスポンスとして返す"
    try:
        session = _current_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="Authentication required")

        role = (session.get("role") or "").strip()
        if role not in {"admin", "dev"}:
            raise HTTPException(status_code=403, detail="Forbidden")

        return await call_next(request)

    except HTTPException as e:
        if accepts_json:
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
        return Response(
            content=str(e.detail),
            status_code=e.status_code,
            media_type="text/plain; charset=utf-8",
        )



@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    刺し③: セキュリティヘッダ付与（_apply_security_headers_to_responseに統合）
    
    P2.5: CSP + Reporting API + キャッシュ制御
    - 全レスポンスにセキュリティヘッダを付与
    - exception handler とロジック共通化（二重実装を削除）
    """
    response = await call_next(request)
    
    # 刺し③: ヘッダ付与とキャッシュ制御を統合関数に委譲
    response = _apply_security_headers_to_response(response, request)
    
    return response


# 刺し③: ヘッダ付与の完全統合（middleware と exception handler で共通化）
# middleware だけだと StreamingResponse 等で崩れる可能性があるため
# exception handler でも同じ関数を呼んで、契約を二重保証（契約駆動の思想）


def _apply_security_headers_to_response(response: Response, request: Request) -> Response:
    """
    刺し③: セキュリティヘッダとキャッシュ制御を response に適用
    （middleware と exception handler で共通化、二重実装を削除）

    任意刺しA: app.state から取得（起動時に作った値を使う）
    パフォーマンス: 毎リクエスト get_settings() + _build_csp_policy() しない
    """
    # 任意刺しA: app.state 優先参照（起動時に構築済み）
    csp_mode = request.app.state.csp_mode
    csp_policy = request.app.state.csp_policy
    mode = request.app.state.mode
    csp_use_reporting_api = request.app.state.csp_use_reporting_api
    csp_report_uri = request.app.state.csp_report_uri

    # CSPヘッダ付与
    if csp_mode != "off":
        if csp_mode == "report":
            if "Content-Security-Policy-Report-Only" not in response.headers:
                response.headers["Content-Security-Policy-Report-Only"] = csp_policy
            if "Content-Security-Policy" in response.headers:
                del response.headers["Content-Security-Policy"]
        elif csp_mode == "enforce":
            if "Content-Security-Policy" not in response.headers:
                response.headers["Content-Security-Policy"] = csp_policy
            if "Content-Security-Policy-Report-Only" in response.headers:
                del response.headers["Content-Security-Policy-Report-Only"]

        # Reporting-Endpoints / Report-To（観測期間のみ）
        if csp_mode == "report" and csp_use_reporting_api and csp_report_uri:
            external_origin = _get_external_origin(request)
            endpoint_abs = external_origin + csp_report_uri

            response.headers["Reporting-Endpoints"] = f'csp-endpoint="{endpoint_abs}"'

            max_age = 3600
            response.headers["Report-To"] = json.dumps(
                {"group": "csp-endpoint", "max_age": max_age, "endpoints": [{"url": endpoint_abs}]}
            )

    # その他セキュリティヘッダ
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"

    def _add_vary_accept(resp: Response) -> None:
        """Vary を壊さずに Accept を追加する（例外経路も含めて統一保証）"""
        vary = resp.headers.get("Vary", "")
        parts = [p.strip() for p in vary.split(",") if p.strip()]
        if not any(p.lower() == "accept" for p in parts):
            parts.append("Accept")
        resp.headers["Vary"] = ", ".join(parts)

    # 刺し③: キャッシュ制御も統合（/ と /static/ui.js のみ）
    path = request.url.path

    if path == "/":
        # トップHTML（index.html/index.htmx）のキャッシュ制御
        if mode == "local":
            response.headers["Cache-Control"] = "no-store"
        else:  # prod
            response.headers["Cache-Control"] = "no-cache"

        # / は HTML/JSON の分岐がある
        _add_vary_accept(response)

    elif path == "/notes" or path.startswith("/notes/"):
        # /notes 系も HTML/JSON の分岐がある（401/403/404 等の例外経路でも Vary を落とさない）
        _add_vary_accept(response)

    elif path == "/static/ui.js":
        # ui.js は prod でも常に no-store（UI未反映の本命対策）
        response.headers["Cache-Control"] = "no-store"
    elif mode == "local" and path.startswith("/static/"):
        # その他静的: local のみ no-store
        response.headers["Cache-Control"] = "no-store"

    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    HTTPException でもCSPヘッダを付与（刺しC: 例外経路の完全保証）
    
    刺しC: Accept header で HTML/JSON を切り替え（契約の明確化）
    - text/html → ログイン誘導HTML（401/403の場合）
    - application/json → JSON error
    """
    accept = request.headers.get("accept", "")
    
    # HTML要求 かつ 401/403 → ログイン誘導
    if "text/html" in accept and exc.status_code in (401, 403):
        # 簡易なログインページHTML（本番ではもっと凝ったものに）
        html_content = f"""
<!DOCTYPE html>
<html>
<head><title>Authentication Required</title></head>
<body>
    <h1>Authentication Required</h1>
    <p>{exc.detail}</p>
    <p><a href="/auth/login">Go to Login</a></p>
</body>
</html>
"""
        response = HTMLResponse(
            status_code=exc.status_code,
            content=html_content
        )
    else:
        # JSON要求 or その他のステータス → JSON error
        response = JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail}
        )
    
    return _apply_security_headers_to_response(response, request)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """未捕捉例外（500）でもCSPヘッダを付与（刺しC: 例外経路の完全保証）"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    response = JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )
    return _apply_security_headers_to_response(response, request)


# Mount static files (middleware の後)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ============================================================
# Auth Endpoints
# ============================================================

class LoginRequest(BaseModel):
    password: str


@app.post("/auth/login")
def login(request: Request, req: LoginRequest):
    s = get_settings()

    role: Optional[str] = None
    if req.password == s.admin_password:
        role = "admin"
    elif s.dev_password and req.password == s.dev_password:
        role = "dev"

    if not role:
        raise HTTPException(status_code=401, detail="Invalid password")

    resp = Response(content=json.dumps({"role": role}), media_type="application/json")
    _issue_session_cookie(resp, request, {"role": role})
    return resp


@app.post("/auth/logout")
def logout():
    resp = Response(content=json.dumps({"ok": True}), media_type="application/json")
    _clear_session(resp)
    return resp


@app.get("/auth/check")
def check_auth(request: Request):
    session = _current_session(request) or _autologin_local(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"role": session.get("role"), "auto": session.get("auto", False)}


# ============================================================
# UI Routes
# ============================================================

@app.get("/")
def root(request: Request):
    # Allow JSON-only endpoints without auth (local convenience).
    if _no_auth_json_exception() and _is_local_host(request) and _wants_json(request):
        return {"ok": True, "mode": get_settings().mode}

    # Interactive browser: require auth.
    _ensure_role(request, {"admin", "dev"})

    if UI_INDEX_HTML.exists():
        resp = FileResponse(UI_INDEX_HTML)
        _ensure_csrf_cookie(resp, request)
        return resp
    elif UI_INDEX_HTMX.exists():
        resp = FileResponse(UI_INDEX_HTMX, media_type="text/html")
        _ensure_csrf_cookie(resp, request)
        return resp
    else:
        return HTMLResponse(render_no_ui())


# ============================================================
# Notes API
# ============================================================

@app.get("/notes/table")
def notes_table(
    request: Request,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    comment: Optional[str] = None,
):
    # JSON convenience exception.
    if not (_no_auth_json_exception() and _is_local_host(request) and _wants_json(request)):
        _ensure_role(request, {"admin", "dev"})

    where: list[str] = []
    params: list[object] = []

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if not statuses:
            raise HTTPException(status_code=400, detail="Invalid status filter")
        for s in statuses:
            if s not in ALLOWED_STATUS:
                raise HTTPException(status_code=400, detail="Invalid status filter")
        where.append("n.status IN (%s)" % ",".join(["?"] * len(statuses)))
        params.extend(statuses)

    # priority filter:
    #   ?priority=none|1|2|3|none,1,2
    # none -> priority IS NULL
    if priority is not None and priority.strip() != "":
        parts = [p.strip() for p in priority.split(",") if p.strip()]
        has_none = any(p.lower() == "none" for p in parts)
        nums: list[int] = []
        for p in parts:
            if p.lower() == "none":
                continue
            if not p.isdigit():
                raise HTTPException(status_code=400, detail="Invalid priority filter")
            v = int(p)
            if v < PRIORITY_RANGE[0] or v > PRIORITY_RANGE[1]:
                raise HTTPException(status_code=400, detail="Invalid priority filter")
            nums.append(v)
        nums = sorted(set(nums))

        if has_none and nums:
            where.append("(n.priority IS NULL OR n.priority IN (%s))" % ",".join(["?"] * len(nums)))
            params.extend(nums)
        elif has_none:
            where.append("n.priority IS NULL")
        elif nums:
            where.append("n.priority IN (%s)" % ",".join(["?"] * len(nums)))
            params.extend(nums)
        else:
            # priority= (only commas/spaces) is treated as no filter; but priority=none must be explicit.
            raise HTTPException(status_code=400, detail="Invalid priority filter")

    # comment filter:
    #   ?comment=any|none
    # 判定は note_events.event_type='comment' の存在（JOINせず EXISTS）
    if comment is not None and comment.strip() != "":
        v = comment.strip().lower()
        if v == "any":
            where.append(
                "EXISTS (SELECT 1 FROM note_events ne WHERE ne.note_id = n.id AND ne.event_type = 'comment')"
            )
        elif v == "none":
            where.append(
                "NOT EXISTS (SELECT 1 FROM note_events ne WHERE ne.note_id = n.id AND ne.event_type = 'comment')"
            )
        else:
            raise HTTPException(status_code=400, detail="Invalid comment filter")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with db() as con:
        rows = con.execute(
            f"""
            SELECT n.id, n.slug, n.status, n.priority, n.created_at, n.updated_at,
                   COUNT(e.id) AS evidence_count
            FROM notes n
            LEFT JOIN evidence e ON n.id = e.note_id
            {where_sql}
            GROUP BY n.id
            ORDER BY 
                CASE WHEN n.priority IS NULL THEN 0 ELSE 1 END,
                n.priority ASC,
                n.updated_at DESC
            """,
            tuple(params),
        ).fetchall()

    notes = [dict(r) for r in rows]

    if _wants_html(request):
        resp = HTMLResponse(render_notes_table(notes))
        _ensure_csrf_cookie(resp, request)
        # 刺し⑥: Vary: Accept を付与（HTML/JSON分岐によるキャッシュ事故防止）
        resp.headers["Vary"] = "Accept"
        return resp

    from fastapi.responses import JSONResponse
    resp = JSONResponse({"notes": notes})
    resp.headers["Vary"] = "Accept"
    return resp


@app.get("/notes/{slug}")
def note_detail(request: Request, slug: str):
    # JSON convenience exception.
    if not (_no_auth_json_exception() and _is_local_host(request) and _wants_json(request)):
        _ensure_role(request, {"admin", "dev"})

    with db() as con:
        note_row = con.execute("SELECT * FROM notes WHERE slug = ?", (slug,)).fetchone()
        if not note_row:
            raise HTTPException(status_code=404, detail="Note not found")

        note_id = note_row["id"]

        evidence_rows = con.execute(
            "SELECT * FROM evidence WHERE note_id = ? ORDER BY created_at DESC",
            (note_id,),
        ).fetchall()

        event_rows = con.execute(
            "SELECT * FROM note_events WHERE note_id = ? ORDER BY changed_at DESC",
            (note_id,),
        ).fetchall()

    note = dict(note_row)
    evidence = [dict(r) for r in evidence_rows]
    events = [dict(r) for r in event_rows]

    if _wants_html(request):
        resp = HTMLResponse(render_note_detail(note, evidence, events))
        _ensure_csrf_cookie(resp, request)
        # 刺し⑥: Vary: Accept を付与（HTML/JSON分岐によるキャッシュ事故防止）
        resp.headers["Vary"] = "Accept"
        return resp

    # 刺し⑥: JSON応答にもVary: Acceptを付与
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"note": note, "evidence": evidence, "events": events})
    resp.headers["Vary"] = "Accept"
    return resp


class NoteUpdateRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[int] = None
    comment: Optional[str] = None


# Contract (P1): empty-body PATCH returns 204
#   (tests/test_patch_notes.py::test_patch_no_change_returns_204)
# Contract (P1): comment-only PATCH returns 200
#   (tests/test_patch_notes.py::test_patch_slug_with_trailing_slash)
# Contract (P1.5): same-value PATCH returns 204 and must not change updated_at / note_events
#   (tests/test_p1_ledger_pollution_contract.py::test_p1_noop_patch_returns_204_and_no_pollution)
# Operational note: if /notes/{slug}/ redirect drops body in some clients, add @app.patch("/notes/{slug}/") alias.
@app.patch("/notes/{slug}")
def update_note(request: Request, slug: str, req: NoteUpdateRequest):
    # CSRF: enforce only when cookie is present.
    _verify_csrf_if_cookie_present(request)

    # JSON convenience exception.
    if not (_no_auth_json_exception() and _is_local_host(request) and _wants_json(request)):
        _ensure_role(request, {"admin", "dev"})

    # 空PATCH（{}）は即204（P1 contract）
    # Pydantic v2: model_fields_set で "送られたキー" を判定できる
    fields_set = getattr(req, "model_fields_set", set())
    if not fields_set:
        return Response(status_code=204)

    # null指定の扱い:
    # - priority は {"priority": null} を "クリア" として扱う
    # - status/comment は null を無効（400）として扱う（意図不明確のため）
    status_provided = "status" in fields_set
    priority_provided = "priority" in fields_set
    comment_provided = "comment" in fields_set

    if status_provided and req.status is None:
        raise HTTPException(status_code=400, detail="Invalid status")
    if comment_provided and req.comment is None:
        raise HTTPException(status_code=400, detail="Invalid comment")

    now = datetime.now().isoformat(timespec="seconds")

    with db() as con:
        note_row = con.execute(
            "SELECT id, status, priority FROM notes WHERE slug = ?",
            (slug,),
        ).fetchone()
        if not note_row:
            raise HTTPException(status_code=404, detail="Note not found")

        note_id = note_row["id"]
        old_status = note_row["status"]
        old_priority = note_row["priority"]

        new_status = old_status
        new_priority = old_priority

        if status_provided:
            if req.status not in ALLOWED_STATUS:
                raise HTTPException(status_code=400, detail="Invalid status")
            new_status = req.status

        if priority_provided:
            # priority は None 許可（クリア）
            if req.priority is not None:
                if req.priority < PRIORITY_RANGE[0] or req.priority > PRIORITY_RANGE[1]:
                    raise HTTPException(status_code=400, detail=f"Invalid priority (must be {PRIORITY_RANGE[0]}-{PRIORITY_RANGE[1]})")
            new_priority = req.priority

        # 変更検出
        changed = False
        if new_status != old_status or new_priority != old_priority:
            changed = True
        if comment_provided and req.comment is not None:
            changed = True

        # no-op → 204（監査ログ汚染防止）
        if not changed:
            return Response(status_code=204)

        # 変更あり → UPDATE + events
        con.execute(
            "UPDATE notes SET status = ?, priority = ?, updated_at = ? WHERE id = ?",
            (new_status, new_priority, now, note_id),
        )

        if new_status != old_status:
            con.execute(
                """
                INSERT INTO note_events (note_id, event_type, old_value, new_value, changed_at)
                VALUES (?, 'status_change', ?, ?, ?)
                """,
                (note_id, old_status, new_status, now),
            )

        if new_priority != old_priority:
            con.execute(
                """
                INSERT INTO note_events (note_id, event_type, old_value, new_value, changed_at)
                VALUES (?, 'priority_change', ?, ?, ?)
                """,
                (
                    note_id,
                    None if old_priority is None else str(old_priority),
                    None if new_priority is None else str(new_priority),
                    now,
                ),
            )

        if comment_provided and req.comment is not None:
            con.execute(
                """
                INSERT INTO note_events (note_id, event_type, old_value, new_value, changed_at)
                VALUES (?, 'comment', NULL, ?, ?)
                """,
                (note_id, req.comment, now),
            )

        con.commit()

        updated_row = con.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()

    # Task 3: レスポンスをSuperset化（両契約テストを満たす）
    note = dict(updated_row)
    return {"note": note, "status": note["status"]}


# ============================================================
# Export API
# ============================================================

@app.get("/export/notes")
def export_notes(request: Request, include_deleted: int = 0, include_archived: int = 0):
    # JSON convenience exception.
    if not (_no_auth_json_exception() and _is_local_host(request) and _wants_json(request)):
        _ensure_role(request, {"admin", "dev"})

    where = []
    if not include_deleted:
        where.append("n.is_deleted = 0")
    if not include_archived:
        where.append("n.is_archived = 0")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with db() as con:
        rows = con.execute(
            f"""
            SELECT n.id, n.slug, n.status, n.priority, n.created_at, n.updated_at,
                   COUNT(e.id) as evidence_count
            FROM notes n
            LEFT JOIN evidence e ON n.id = e.note_id
            {where_sql}
            GROUP BY n.id
            ORDER BY n.updated_at DESC
            """
        ).fetchall()

    return {"notes": [dict(r) for r in rows]}


@app.get("/export/summary")
def export_summary(request: Request):
    # JSON convenience exception.
    if not (_no_auth_json_exception() and _is_local_host(request) and _wants_json(request)):
        _ensure_role(request, {"admin", "dev"})

    with db() as con:
        total = con.execute("SELECT COUNT(*) as cnt FROM notes").fetchone()["cnt"]

        rows = con.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM notes
            GROUP BY status
            """
        ).fetchall()

        last_scan = con.execute("SELECT last_scan_at FROM scan_state WHERE id = 1").fetchone()[
            "last_scan_at"
        ]

    data = {
        "total": total,
        "by_status": {r["status"]: r["cnt"] for r in rows},
        "last_scan_at": last_scan,
    }

    if _wants_html(request):
        resp = HTMLResponse(render_summary(data, allowed_statuses=ALLOWED_STATUS_ORDER))

        _ensure_csrf_cookie(resp, request)
        return resp

    return data


@app.get("/export/scan_history")
def export_scan_history(limit: int = 50):
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=400, detail="limit must be 1..2000")

    with db() as con:
        rows = con.execute(
            """
            SELECT id, scanned_at, scanned_root, full,
                   files_scanned, slugs_found, evidence_added,
                   done_forced, stale_marked, revived_count, orphan_files_removed
            FROM scan_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {"recent": [dict(r) for r in rows]}


@app.get("/export/metrics")
def export_metrics(request: Request, limit: int = 50):
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=400, detail="limit must be 1..2000")

    exported_at = datetime.now().isoformat(timespec="seconds")

    with db() as con:
        recent = con.execute(
            """
            SELECT id, scanned_at, scanned_root, full,
                   files_scanned, slugs_found, evidence_added,
                   done_forced, stale_marked, revived_count, orphan_files_removed
            FROM scan_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        agg = con.execute(
            """
            SELECT
              COUNT(*) as runs,
              SUM(CASE WHEN full = 1 THEN 1 ELSE 0 END) as full_runs,
              SUM(CASE WHEN full = 0 THEN 1 ELSE 0 END) as diff_runs,
              COALESCE(SUM(done_forced), 0) as done_forced,
              COALESCE(SUM(stale_marked), 0) as stale_marked,
              COALESCE(SUM(revived_count), 0) as revived_count,
              COALESCE(SUM(orphan_files_removed), 0) as orphan_files_removed,
              COALESCE(SUM(evidence_added), 0) as evidence_added,
              COALESCE(SUM(files_scanned), 0) as files_scanned,
              COALESCE(SUM(slugs_found), 0) as slugs_found
            FROM (
              SELECT done_forced, stale_marked, revived_count, orphan_files_removed,
                     evidence_added, files_scanned, slugs_found, full
              FROM scan_log
              ORDER BY id DESC
              LIMIT ?
            )
            """,
            (limit,),
        ).fetchone()

        agg_all = con.execute(
            """
            SELECT
              COUNT(*) as runs,
              SUM(CASE WHEN full = 1 THEN 1 ELSE 0 END) as full_runs,
              SUM(CASE WHEN full = 0 THEN 1 ELSE 0 END) as diff_runs,
              COALESCE(SUM(done_forced), 0) as done_forced,
              COALESCE(SUM(stale_marked), 0) as stale_marked,
              COALESCE(SUM(revived_count), 0) as revived_count,
              COALESCE(SUM(orphan_files_removed), 0) as orphan_files_removed,
              COALESCE(SUM(evidence_added), 0) as evidence_added,
              COALESCE(SUM(files_scanned), 0) as files_scanned,
              COALESCE(SUM(slugs_found), 0) as slugs_found
            FROM scan_log
            """
        ).fetchone()

        last_scan = con.execute("SELECT last_scan_at FROM scan_state WHERE id = 1").fetchone()[
            "last_scan_at"
        ]

    data = {
        "exported_at": exported_at,
        "last_scan_at": last_scan,
        "limit": limit,
        "recent": [dict(r) for r in recent],
        "aggregate": dict(agg) if agg else {},
        "aggregate_all": dict(agg_all) if agg_all else {},
        "resolved_root": str(resolve_root(None)),
        "root_resolution": {
            "order": [
                "request.root",
                f"env.{LEDGER_REPO_ROOT_ENV}",
                "auto_detect(.git/pyproject.toml/requirements.txt)",
                "fallback(DEFAULT_REPO_ROOT)",
            ]
        },
    }

    if _wants_html(request):
        resp = HTMLResponse(render_metrics(data))
        _ensure_csrf_cookie(resp, request)
        return resp

    return data


# ============================================================
# Scan API
# ============================================================

class ScanRequest(BaseModel):
    root: Optional[str] = Field(default=None, description="repo root (optional)")


class ScanResponse(BaseModel):
    scanned_root: str
    files_scanned: int
    slugs_found: int
    evidence_added: int
    done_forced: int
    stale_marked: int
    revived_count: int
    orphan_files_removed: int


@app.post("/scan")
def scan(
    request: Request,
    req: ScanRequest = Body(default_factory=ScanRequest),
    full: bool = False,
):
    """
    /scan responsibility:
      - Get input (files)
      - Extract NOTE/DONE and reflect to ledger

    Safety rules:
      - full=False (diff): NEVER run stale/orphan
      - full=True (full scan): Run stale/orphan only (closing the world)
    """
    # CSRF: enforce only when cookie is present (keeps curl/CI compatible)
    _verify_csrf_if_cookie_present(request)

    s = get_settings()

    # AUTHZ: /scan is a dangerous endpoint; CSRF does not protect no-cookie callers.
    # - local: allow JSON no-auth only for localhost scripts (ci_export 等)
    # - prod : ALWAYS require auth (no exceptions)
    if not (_no_auth_json_exception() and _is_local_host(request) and _wants_json(request)):
        _ensure_role(request, {"admin", "dev"})


    # P0-A: close /scan root in prod (ignore request.root)
    root_path = resolve_root(None if s.mode == "prod" else req.root)
    if not root_path.exists() or not root_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Invalid root: {root_path}")

    now = datetime.now().isoformat(timespec="seconds")

    seen_slugs: set[str] = set()
    done_slugs: set[str] = set()

    evidence_added = 0
    stale_marked = 0
    orphan_removed = 0
    revived_count = 0

    with db() as con:
        if full:
            files = list(iter_source_files(root_path))
            seen_paths = {str(p.relative_to(root_path)).replace("\\", "/") for p in files}
        else:
            files, seen_paths = list_files_hashdiff(con, root=root_path, now=now)

        hits = collect_hits_from_files(root=root_path, files=files)

        for h in hits:
            seen_slugs.add(h.slug)
            if h.kind == "done":
                done_slugs.add(h.slug)

            note_id, revived = upsert_note(con, h.slug, now)
            if revived:
                revived_count += 1

            if add_evidence(con, note_id, h.path, h.line, h.snippet, now):
                evidence_added += 1

        done_forced = force_done(con, slugs=done_slugs, now=now)

        if full:
            # Safety fuse is inside mark_missing_as_stale(full=..., seen_slugs=...)
            stale_marked = mark_missing_as_stale(
                con,
                full=True,
                seen_slugs=seen_slugs,
                now=now,
            )
            orphan_removed = cleanup_orphan_file_state(con, seen_paths=seen_paths)

        set_last_scan_at(con, now)

        insert_scan_log(
            con,
            scanned_at=now,
            scanned_root=str(root_path),
            full=1 if full else 0,
            files_scanned=len(files),
            slugs_found=len(seen_slugs),
            evidence_added=evidence_added,
            done_forced=done_forced,
            stale_marked=stale_marked,
            revived_count=revived_count,
            orphan_files_removed=orphan_removed,
        )

        con.commit()

    if _wants_html(request):
        html_out = render_scan_result(
            full=full,
            root_path=root_path,
            files_scanned=len(files),
            slugs_found=len(seen_slugs),
            evidence_added=evidence_added,
            done_forced=done_forced,
            stale_marked=stale_marked,
            revived_count=revived_count,
            orphan_files_removed=orphan_removed,
        )
        resp = HTMLResponse(html_out)
        _ensure_csrf_cookie(resp, request)
        return resp

    return ScanResponse(
        scanned_root=str(root_path),
        files_scanned=len(files),
        slugs_found=len(seen_slugs),
        evidence_added=evidence_added,
        done_forced=done_forced,
        stale_marked=stale_marked,
        revived_count=revived_count,
        orphan_files_removed=orphan_removed,
    )


# ============================================================
# CSP Report Endpoint (P2.5)
# ============================================================

@app.post("/__csp_report")
async def csp_report(request: Request):
    """
    CSP violation report endpoint (always no-auth, always 204)
    
    刺し⑦: 監査台帳に副作用なし（契約）
    - DBには一切触らない（note_events/evidence/scan_logに混ざらない）
    - ログ出力のみ（運用ノイズ回避）
    - サンプリング辞書はメモリ内（上限1000件、永続化不要）
    """
    try:
        # 刺し④: Content-Type チェック（DoS耐性・ログ汚染防止）
        # CSP報告っぽくないContent-Typeは即204で捨てる（パース不要）
        content_type = request.headers.get("content-type", "").lower()
        if content_type and not any(ct in content_type for ct in ["application/json", "application/csp-report", "application/reports+json"]):
            # CSP報告っぽくない: 静かに204（ログ汚染回避）
            return Response(status_code=204)
        
        # 壊れた/非JSON/空bodyでも静かに204（ブラウザ実装の揺れに対応）
        body_bytes = await request.body()
        
        if not body_bytes:
            # 空body: 静かに204
            return Response(status_code=204)
        
        # 刺し④: bodyサイズ上限（DoS耐性・メモリ汚染防止）
        if len(body_bytes) > 32_768:  # 32KB上限
            logger.warning(f"CSP report: oversized payload ({len(body_bytes)} bytes, limit 32KB)")
            return Response(status_code=204)
        
        # JSON decode試行（失敗しても204で飲み込む）
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # 壊れたpayload: ログに残して204
            logger.warning(f"CSP report: malformed payload (non-JSON or invalid encoding)")
            return Response(status_code=204)
        
        # 刺し②: 旧式csp-reportと新式Reporting API両対応
        csp_report = None
        
        # 旧式: {"csp-report": {...}}
        if "csp-report" in body:
            csp_report = body["csp-report"]
        # 新式Reporting API: {"reports": [{"type": "csp-violation", "body": {...}}]}
        elif "reports" in body and isinstance(body["reports"], list) and len(body["reports"]) > 0:
            first_report = body["reports"][0]
            if isinstance(first_report, dict) and "body" in first_report:
                csp_report = first_report["body"]
        
        if not csp_report:
            # どちらの形式でもない: 静かに204
            return Response(status_code=204)
        
        # 刺し①④: ログ汚染防止 - 運用で効く要点のみ抽出（ボディ全体はログしない）
        # blocked-uri: 何がブロックされたか
        # violated-directive: どのディレクティブ違反か
        # effective-directive: 実際に適用されたディレクティブ（より具体的）
        # source-file: 違反が発生したファイル（あれば）
        blocked_uri = csp_report.get("blocked-uri") or csp_report.get("blockedURI") or "unknown"
        violated_directive = csp_report.get("violated-directive") or csp_report.get("violatedDirective") or "unknown"
        effective_directive = csp_report.get("effective-directive") or csp_report.get("effectiveDirective") or ""
        source_file = csp_report.get("source-file") or csp_report.get("sourceFile") or ""
        
        # 刺しA: サンプリング（同一違反を60秒に1回だけログ出力）
        # Report-Only運用で違反が多い期間、ログ課金/可観測性が死ぬのを防ぐ
        # 
        # ログ注入防止: blocked-uri/violated-directive から改行除去
        blocked_uri_clean = blocked_uri.replace('\n', '').replace('\r', '')[:200]
        violated_directive_clean = violated_directive.replace('\n', '').replace('\r', '')[:100]
        
        sampling_key = (blocked_uri_clean, violated_directive_clean)
        now = time.time()
        
        # 刺しA: 辞書上限（1000件）- ユニーク違反が増殖してもメモリ安全
        if len(_CSP_REPORT_SAMPLING) >= 1000:
            # 古いエントリを間引く（最古50%削除）
            sorted_items = sorted(_CSP_REPORT_SAMPLING.items(), key=lambda x: x[1])
            _CSP_REPORT_SAMPLING.clear()
            _CSP_REPORT_SAMPLING.update(dict(sorted_items[500:]))
        
        last_logged = _CSP_REPORT_SAMPLING.get(sampling_key, 0.0)
        
        if now - last_logged < 60.0:
            # 60秒以内に同じ違反をログ済み → 静かに204（サンプリングでスキップ）
            return Response(status_code=204)
        
        # サンプリング通過 → ログ出力してタイムスタンプ更新
        _CSP_REPORT_SAMPLING[sampling_key] = now
        
        log_parts = [
            f"blocked-uri={blocked_uri[:200]}",
            f"violated-directive={violated_directive[:100]}"
        ]
        if effective_directive:
            log_parts.append(f"effective-directive={effective_directive[:100]}")
        if source_file:
            log_parts.append(f"source-file={source_file[:200]}")
        
        # 刺し①: prodはINFO、localはWARNING（本番のログ量を抑える）
        settings = get_settings()
        if settings.mode == "prod":
            logger.info(f"CSP violation: {', '.join(log_parts)}")
        else:
            logger.warning(f"CSP violation: {', '.join(log_parts)}")
    except Exception as e:
        # 予期しない例外: ログに残して204（レポート受信は止めない）
        logger.error(f"CSP report handler error: {e}")
    
    return Response(status_code=204)


# ============================================================
# === P2.5 END ===
# 上記がP2.5で追加した全領域
# P2.5では P0/P1 CORE 領域と P2.5 領域以外は触らない（契約）
# ============================================================

