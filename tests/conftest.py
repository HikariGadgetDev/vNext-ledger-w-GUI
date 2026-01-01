# root/vnext-ledger/tests/conftest.py

import os
from pathlib import Path

import pytest
import app
from tests.helpers.auth import AuthClient
from app import db


@pytest.fixture(scope="session", autouse=True)
def _load_settings_for_tests(tmp_path_factory):
    os.environ.setdefault("MODE", "local")
    os.environ.setdefault("SESSION_SECRET", "test-secret")
    os.environ.setdefault("ADMIN_PASSWORD", "admin")
    os.environ.setdefault("DEV_PASSWORD", "dev")

    isolate = os.getenv("VNEXT_TEST_DB_ISOLATION", "1") == "1"

    if isolate:
        # --- Test DB isolation (never touch real ledger.sqlite3) ---
        tmp_dir = tmp_path_factory.mktemp("vnext-ledger-tests")
        db_path = tmp_dir / "ledger.sqlite3"

        os.environ["DB_PATH"] = str(db_path)
        app.DB_PATH = Path(os.environ["DB_PATH"])

    else:
        # Isolation OFF...
        if "DB_PATH" in os.environ and os.environ["DB_PATH"].strip():
            app.DB_PATH = Path(os.environ["DB_PATH"])
            app.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # DB_PATH が無ければ app.DB_PATH をそのまま使う

    # ✅ settings キャッシュを確実にクリア（テストの環境変数反映を保証）
    app._SETTINGS = None  # type: ignore[attr-defined]
    app.init_settings()
    app.init_db()  # Explicitly init DB (no longer called by init_settings)

    # ✅ テストは lifespan を通らないため、app.state をここで初期化する
    # （middleware が request.app.state.* を読む前提になっている）

    settings = app.get_settings()
    app.app.state.csp_mode = settings.csp_mode
    app.app.state.csp_policy = app._build_csp_policy(settings) if settings.csp_mode != "off" else None
    app.app.state.mode = settings.mode
    app.app.state.csp_use_reporting_api = settings.csp_use_reporting_api
    app.app.state.csp_report_uri = settings.csp_report_uri

    yield


def _ensure_note_exists(slug: str = "test") -> None:
    con = db()
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(notes)").fetchall()]
        colset = set(cols)

        values = {}
        if "slug" in colset:
            values["slug"] = slug
        if "status" in colset:
            values["status"] = "open"
        if "first_seen" in colset:
            values["first_seen"] = "1970-01-01T00:00:00Z"
        if "last_seen" in colset:
            values["last_seen"] = "1970-01-01T00:00:00Z"

        if "created_at" in colset:
            values["created_at"] = "1970-01-01T00:00:00Z"
        if "updated_at" in colset:
            values["updated_at"] = "1970-01-01T00:00:00Z"
        if "is_deleted" in colset:
            values["is_deleted"] = 0
        if "is_archived" in colset:
            values["is_archived"] = 0
        if "title" in colset:
            values["title"] = "test note"

        cols_sql = ", ".join(values.keys())
        qs_sql = ", ".join(["?"] * len(values))
        con.execute(
            f"INSERT OR IGNORE INTO notes ({cols_sql}) VALUES ({qs_sql})",
            tuple(values.values()),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def client():
    _ensure_note_exists("test")
    return AuthClient()


@pytest.fixture
def test_db():
    return app.DB_PATH


@pytest.fixture
def temp_repo(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def reset_app_state():
    """
    刺し②: 全テストで自動的にアプリ状態をリセット（autouse=True）
    
    現状のtest_csp_headers.pyは毎回「退避→復元」の儀式をしてる。
    P3/P4で状態が増えた瞬間に「戻し忘れ」が出るリスクを防ぐ。
    
    Note:
        この fixture は autouse=True なので、全テストで自動適用される。
        個別のテストから「退避→復元」のコードを削除できる（将来的に）。
    """
    # ---- Before ----
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()

    # CSP sampling (DoS/ログ燃え対策の辞書) も退避
    old_csp_sampling = app._CSP_REPORT_SAMPLING.copy()

    # ルート汚染防止：router.routes を退避して復元
    old_routes = app.app.router.routes.copy()

    # app.state も退避（CSP系テストの汚染を防ぐ）
    state_keys = (
        "csp_mode",
        "csp_policy",
        "mode",
        "csp_use_reporting_api",
        "csp_report_uri",
    )
    old_state = {k: getattr(app.app.state, k, None) for k in state_keys}
    old_state_has = {k: hasattr(app.app.state, k) for k in state_keys}

    yield

    # ---- After ----
    app._SETTINGS = old_settings
    app.DB_PATH = old_db_path
    app._PRAGMA_APPLIED_DBS.clear()
    app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)

    app._CSP_REPORT_SAMPLING.clear()
    app._CSP_REPORT_SAMPLING.update(old_csp_sampling)

    app.app.router.routes = old_routes

    for k in state_keys:
        if not old_state_has[k]:
            if hasattr(app.app.state, k):
                delattr(app.app.state, k)
        else:
            setattr(app.app.state, k, old_state[k])
