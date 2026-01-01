"""
刺し⑤: 監査データが消えない契約テスト

P2.5（CSP実装）で監査テーブルが欠損しないことを保証。
- note_events が存在して読み出せる
- evidence が存在して読み出せる
- scan_log が存在して読み出せる

件数や中身の断定はしない（壊れやすい）。
「テーブルが消えてない / 参照できる」だけを契約化。
"""

import sys
from pathlib import Path


import app
from starlette.testclient import TestClient
import pytest


def test_note_events_table_exists_and_readable(monkeypatch, tmp_path):
    """刺し⑤: note_events テーブルが存在して読み出せる（欠損防止）"""
    # Setup
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "off")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
    
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    
    # Act: DBを初期化（lifespan経由）
    with TestClient(app.app) as client:
        # テーブルが存在するか確認
        import sqlite3
        con = sqlite3.connect(db_path)
        cursor = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='note_events'")
        table_exists = cursor.fetchone()
        
        # Assert: テーブルが存在する
        assert table_exists is not None, "note_events テーブルが存在すること（監査データ欠損防止）"
        
        # Assert: 読み出せる（構造が壊れてない）
        cursor = con.execute("SELECT id, note_id, event_type, changed_at FROM note_events LIMIT 0")
        assert cursor.description is not None, "note_events が読み出せること（構造が壊れてない）"
        
        con.close()


def test_evidence_table_exists_and_readable(monkeypatch, tmp_path):
    """刺し⑤: evidence テーブルが存在して読み出せる（欠損防止）"""
    # Setup
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "off")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
    
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    
    # Act
    with TestClient(app.app) as client:
        import sqlite3
        con = sqlite3.connect(db_path)
        cursor = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evidence'")
        table_exists = cursor.fetchone()
        
        # Assert: テーブルが存在する
        assert table_exists is not None, "evidence テーブルが存在すること（監査データ欠損防止）"
        
        # Assert: 読み出せる
        cursor = con.execute("SELECT id, note_id, filepath, created_at FROM evidence LIMIT 0")
        assert cursor.description is not None, "evidence が読み出せること（構造が壊れてない）"
        
        con.close()


def test_scan_log_table_exists_and_readable(monkeypatch, tmp_path):
    """刺し⑤: scan_log テーブルが存在して読み出せる（欠損防止）"""
    # Setup
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "off")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
    
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    
    # Act
    with TestClient(app.app) as client:
        import sqlite3
        con = sqlite3.connect(db_path)
        cursor = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_log'")
        table_exists = cursor.fetchone()
        
        # Assert: テーブルが存在する
        assert table_exists is not None, "scan_log テーブルが存在すること（監査データ欠損防止）"
        
        # Assert: 読み出せる
        cursor = con.execute("SELECT id, scanned_at, scanned_root FROM scan_log LIMIT 0")
        assert cursor.description is not None, "scan_log が読み出せること（構造が壊れてない）"
        
        con.close()


def test_export_endpoints_accessible(monkeypatch, tmp_path):
    """刺し①: export系エンドポイントが動作する（監査データ参照可能）
    契約: local + localhost + JSON のときは no-auth で通る（B方針）
    """
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "off")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    # Re-init（このテスト内で env/DB を差し替える）
    app._SETTINGS = None  # type: ignore[attr-defined]
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()

    app.init_settings()
    app.init_db()

    # lifespan 相当の state 初期化（middleware 前提）
    settings = app.get_settings()
    app.app.state.csp_mode = settings.csp_mode
    app.app.state.csp_policy = app._build_csp_policy(settings) if settings.csp_mode != "off" else None
    app.app.state.mode = settings.mode
    app.app.state.csp_use_reporting_api = settings.csp_use_reporting_api
    app.app.state.csp_report_uri = settings.csp_report_uri

    with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
        headers = {"Accept": "application/json"}

        r_notes = client.get("/export/notes", headers=headers)
        assert r_notes.status_code == 200, "export/notes が動作すること"

        r_summary = client.get("/export/summary", headers=headers)
        assert r_summary.status_code == 200, "export/summary が動作すること"

        r_scan = client.get("/export/scan_history", headers=headers)
        assert r_scan.status_code == 200, "export/scan_history が動作すること（scan_log参照）"

        r_metrics = client.get("/export/metrics", headers=headers)
        assert r_metrics.status_code == 200, "export/metrics が動作すること"


def test_scan_endpoint_writes_to_db(monkeypatch, tmp_path):
    """刺し①: /scan が動作してDBに書き込める（監査データ書き込み可能）
    契約: local + localhost + JSON のときは no-auth で通る（B方針）
    """
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "off")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    (scan_root / "test.py").write_text("# TODO-ABC: test note\n", encoding="utf-8")

    # Re-init
    app._SETTINGS = None  # type: ignore[attr-defined]
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()

    app.init_settings()
    app.init_db()

    # lifespan 相当の state 初期化（middleware 前提）
    settings = app.get_settings()
    app.app.state.csp_mode = settings.csp_mode
    app.app.state.csp_policy = app._build_csp_policy(settings) if settings.csp_mode != "off" else None
    app.app.state.mode = settings.mode
    app.app.state.csp_use_reporting_api = settings.csp_use_reporting_api
    app.app.state.csp_report_uri = settings.csp_report_uri

    with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
        resp = client.post(
            "/scan",
            json={"root": str(scan_root), "full": True},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200, "/scan が動作すること"

    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        scan_log_count = con.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]
        assert scan_log_count > 0, "scan_log にスキャン結果が記録されること"
    finally:
        con.close()
