# tests/test_p1_hardening.py
from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app


def _load_isolated_app_module(app_py: Path):
    """
    conftest.py が import app 済みでも影響しないように、
    app.py を “別モジュール名” でロードして分離する。
    """
    name = f"_isolated_app_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, app_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_prod_gate_blocks_startup_and_db_init(monkeypatch, tmp_path):
    """
    (1) “起動時に落ちる”の統合テスト（最重要）
    - MODE=prod & P1_CONTRACT_VERIFIEDなし → lifespan中に落ちる
    - その時点で DB が作られていない（fail-fast / DB init前）こと
    """
    db_path = tmp_path / "prod_gate_should_not_create.sqlite3"

    # prod の必須環境
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
    monkeypatch.setenv("DB_PATH", str(db_path))

    # CIフラグ無し（ここが要点）
    monkeypatch.delenv("P1_CONTRACT_VERIFIED", raising=False)

    isolated = _load_isolated_app_module(Path(app.__file__).resolve())

    # startup(lifespan) で gate が落とす
    with pytest.raises(RuntimeError):
        with TestClient(isolated.app):
            pass

    # gate が落ちたので DB 初期化は走ってないはず（ファイルが存在しない）
    assert not db_path.exists(), "DB must not be initialized when prod gate fails (fail fast)"


def test_csrf_cookie_requires_header_on_scan(client, temp_repo: Path):
    """
    (2) CSRF “cookieがある時だけ強制”
    - cookieあり＆ヘッダ無し → 403
    - cookieあり＆一致ヘッダ → 200
    """
    slug = "p1_csrf_contract"
    p = temp_repo / "csrf.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")

    token = "tok-test"
    # cookieがある状況を人工的に作る（値は任意でOK）
    client.cookies.set(app.CSRF_COOKIE, token)

    r = client.post("/scan?full=1", json={"root": str(temp_repo)})
    assert r.status_code == 403

    r = client.post(
        "/scan?full=1",
        json={"root": str(temp_repo)},
        headers={app.CSRF_HEADER: token},
    )
    assert r.status_code == 200


def test_diff_scan_never_marks_stale(client, temp_repo: Path):
    """
    (3) diff scan の“安全 fuse”
    - full=0（diff）では stale を絶対に付けない
    """
    slug = "p1_diff_fuse"
    p = temp_repo / "diff.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")

    r = client.post("/scan?full=1", json={"root": str(temp_repo)})
    assert r.status_code == 200

    # NOTE が消えた（ファイル削除）
    p.unlink()

    r = client.post("/scan?full=0", json={"root": str(temp_repo)})
    assert r.status_code == 200
    assert r.json()["stale_marked"] == 0

    r = client.get(f"/notes/{slug}", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["note"]["status"] == "open"


def test_full_scan_marks_stale_and_revives_to_open(client, temp_repo: Path):
    """
    (4) stale → open の revive
    - full=1 で消失 → stale
    - 再出現 → open に復帰（revived_count が増える）
    """
    slug = "p1_revive_flow"
    p = temp_repo / "revive.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")

    r = client.post("/scan?full=1", json={"root": str(temp_repo)})
    assert r.status_code == 200

    # 消失 → stale
    p.unlink()
    r = client.post("/scan?full=1", json={"root": str(temp_repo)})
    assert r.status_code == 200
    assert r.json()["stale_marked"] >= 1

    r = client.get(f"/notes/{slug}", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["note"]["status"] == "stale"

    # 再出現 → revive(open)
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")
    r = client.post("/scan?full=1", json={"root": str(temp_repo)})
    assert r.status_code == 200
    assert r.json()["revived_count"] >= 1

    r = client.get(f"/notes/{slug}", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["note"]["status"] == "open"


def test_evidence_dedup_by_file_and_line(client, temp_repo: Path):
    """
    (5) evidence重複防止（同一ファイル＆同一行で増殖しない）
    """
    slug = "p1_evidence_dedup"
    p = temp_repo / "evidence.py"
    # 1行目に固定（line_no を不変にする）
    p.write_text(f"# NOTE(vNext): {slug}\nprint('x')\n", encoding="utf-8")

    r = client.post("/scan?full=1", json={"root": str(temp_repo)})
    assert r.status_code == 200
    r = client.post("/scan?full=1", json={"root": str(temp_repo)})
    assert r.status_code == 200

    with app.db() as con:
        note_row = con.execute("SELECT id FROM notes WHERE slug = ?", (slug,)).fetchone()
        assert note_row is not None
        note_id = note_row["id"]

        cnt = con.execute(
            "SELECT COUNT(*) AS c FROM evidence WHERE note_id = ?",
            (note_id,),
        ).fetchone()["c"]

    assert cnt == 1, "Evidence must not duplicate for same (note_id, filepath, line_no)"
