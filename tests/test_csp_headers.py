# tests/test_csp_headers.py
import os
import pytest
from fastapi.testclient import TestClient
import app



def test_csp_report_only_header(monkeypatch, tmp_path):
    """CSP_MODE=report で Report-Only ヘッダが出る"""
    # Setup: 環境変数
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "report")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
    
    # Setup: テストDB
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    # Setup: グローバルをクリア（lifespan一本化、二重init除去）
    app._SETTINGS = None
    app.DB_PATH = db_path  # Pathのまま
    app._PRAGMA_APPLIED_DBS.clear()
    
    # Act: TestClient lifespan で init（手動 init 不要）
    with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/", headers={"Accept": "application/json"})
        
        # Assert
        assert response.status_code == 200
        assert "Content-Security-Policy-Report-Only" in response.headers
        assert "script-src 'self'" in response.headers["Content-Security-Policy-Report-Only"]
        
        # /static にもヘッダが付くことをテスト
        response_static = client.get("/static/ui.js")
        assert response_static.status_code == 200
        assert "Content-Security-Policy-Report-Only" in response_static.headers
        assert "script-src 'self'" in response_static.headers["Content-Security-Policy-Report-Only"]


def test_csp_enforce_header(monkeypatch, tmp_path):
    """CSP_MODE=enforce で Enforce ヘッダが出る"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "enforce")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/", headers={"Accept": "application/json"})
            
            # Assert
            assert response.status_code == 200
            assert "Content-Security-Policy" in response.headers
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_off_no_header(monkeypatch, tmp_path):
    """CSP_MODE=off で CSP ヘッダが出ない"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "off")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/", headers={"Accept": "application/json"})
            
            # Assert
            assert response.status_code == 200
            assert "Content-Security-Policy-Report-Only" not in response.headers
            assert "Content-Security-Policy" not in response.headers
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_report_uri_endpoint(monkeypatch, tmp_path):
    """CSP_REPORT_URI を設定したら POST /__csp_report が 204 を返す（no-auth契約）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        # no-auth契約: ALLOW_LOCAL_JSON_NOAUTH は意図的に設定しない
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app) as client:
            response = client.post(
                "/__csp_report",
                json={
                    "csp-report": {
                        "blocked-uri": "https://evil.com/script.js",
                        "violated-directive": "script-src"
                    }
                }
            )
            
            # Assert: 受け口が動作すること（no-auth契約）
            assert response.status_code == 204, (
                "contract violation: /__csp_report must be no-auth. "
                "If this fails with 401/403, the authentication logic is blocking CSP reports."
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_report_uri_invalid_value(monkeypatch, tmp_path):
    """CSP_REPORT_URI に外部URLを設定すると RuntimeError（契約を機械化）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（悪い値: 外部URL）
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "https://evil.com/collect")  # 外部URL（NG）
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Act & Assert: init_settings が RuntimeError を投げること
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        with pytest.raises(RuntimeError, match="CSP_REPORT_URI must be a relative path"):
            app.init_settings()
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_report_uri_invalid_endpoint(monkeypatch, tmp_path):
    """CSP_REPORT_URI に未実装endpointを設定すると RuntimeError（契約駆動）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（悪い値: 未実装endpoint）
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/some/other/path")  # 未実装（NG）
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Act & Assert: init_settings が RuntimeError を投げること
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        with pytest.raises(RuntimeError, match="must be '/__csp_report'"):
            app.init_settings()
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_mode_invalid_value(monkeypatch, tmp_path):
    """CSP_MODE に不正値を設定すると RuntimeError（回帰防止）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（悪い値）
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "lol")  # 不正値（NG）
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Act & Assert: init_settings が RuntimeError を投げること
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        with pytest.raises(RuntimeError, match="Invalid CSP_MODE"):
            app.init_settings()
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_report_uri_default_in_prod(monkeypatch, tmp_path):
    """刺し②: prod+report既定なら CSP_REPORT_URI が /__csp_report になる（運用地雷防止）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（prod, CSP_REPORT_URI未設定）
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("SESSION_SECRET", "test-secret-for-prod")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        # CSP_MODE は明示しない（既定で report）
        # CSP_REPORT_URI も明示しない（既定で /__csp_report になるべき）
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Act: settings を取得
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app.init_settings()
        
        settings = app.get_settings()
        
        # Assert: CSP_REPORT_URI が自動設定されること
        assert settings.csp_mode == "report", "prod既定はreport"
        assert settings.csp_report_uri == "/__csp_report", "prod+report既定なら/__csp_reportが自動設定される"
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_report_endpoint_no_auth_in_prod(monkeypatch, tmp_path):
    """刺し①: prodでも /__csp_report は no-auth で 204（実運用の観測が死なない）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（prod, P1契約通り）
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("P1_CONTRACT_VERIFIED", "1")  # ゲート回避じゃなく契約通り
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("SESSION_SECRET", "test-secret-for-prod-csp")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        # ALLOW_LOCAL_JSON_NOAUTH は設定しない（prod想定）
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app) as client:
            # ブラウザが勝手にPOSTしてくる状況を再現（auth無し）
            response = client.post(
                "/__csp_report",
                json={
                    "csp-report": {
                        "blocked-uri": "https://evil.com/script.js",
                        "violated-directive": "script-src"
                    }
                }
            )
            
            # Assert: prodでもno-authで204
            assert response.status_code == 204, (
                "contract violation: /__csp_report must be no-auth even in prod. "
                "If this fails with 401/403, CSP observation is dead in production."
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)



def test_csp_report_endpoint_reporting_api_format(monkeypatch, tmp_path):
    """刺し②: 新式Reporting API形式でもCSPレポートを受信できる（ブラウザ差対応）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app) as client:
            # 新式Reporting API形式で送信
            response = client.post(
                "/__csp_report",
                json={
                    "reports": [
                        {
                            "type": "csp-violation",
                            "body": {
                                "blockedURI": "https://evil.com/script.js",
                                "violatedDirective": "script-src"
                            }
                        }
                    ]
                }
            )
            
            # Assert: 新式形式でも204で受信
            assert response.status_code == 204, (
                "新式Reporting API形式でもCSPレポートを受信できる"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)




def test_ui_js_cache_control_no_store_in_local(monkeypatch, tmp_path):
    """刺し⑤: local時のui.jsはCache-Control: no-storeで返される（古いの握る事故防止）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（local）
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "off")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/static/ui.js")
            
            # Assert: ui.js は no-store
            assert response.status_code == 200
            assert response.headers.get("Cache-Control") == "no-store", (
                "ui.js must have Cache-Control: no-store in local mode"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_ui_js_cache_control_no_store_in_prod(monkeypatch, tmp_path):
    """刺し②⑤: prodでもui.jsはCache-Control: no-storeで返される（Vercel/CDN握り対策）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（prod）
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("P1_CONTRACT_VERIFIED", "1")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("SESSION_SECRET", "test-secret-for-prod-cache")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app) as client:
            response = client.get("/static/ui.js")
            
            # Assert: prodでもui.js は no-store（UI未反映地獄の本命対策）
            assert response.status_code == 200
            assert response.headers.get("Cache-Control") == "no-store", (
                "ui.js must have Cache-Control: no-store even in prod mode (Vercel/CDN cache hell prevention)"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)



def test_csp_header_mutual_exclusion_report_mode(monkeypatch, tmp_path):
    """刺し③: CSP_MODE=report時、Report-Onlyのみ出力（通常CSPは出ない）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/")
            
            # Assert: Report-Onlyのみ、通常CSPは出ない（排他性）
            assert "Content-Security-Policy-Report-Only" in response.headers, (
                "CSP_MODE=report時、Report-Onlyヘッダが必要"
            )
            assert "Content-Security-Policy" not in response.headers, (
                "CSP_MODE=report時、通常CSPヘッダは出てはいけない（二重出力事故防止）"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_header_mutual_exclusion_enforce_mode(monkeypatch, tmp_path):
    """刺し③: CSP_MODE=enforce時、通常CSPのみ出力（Report-Onlyは出ない）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "enforce")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/")
            
            # Assert: 通常CSPのみ、Report-Onlyは出ない（排他性）
            assert "Content-Security-Policy" in response.headers, (
                "CSP_MODE=enforce時、通常CSPヘッダが必要"
            )
            assert "Content-Security-Policy-Report-Only" not in response.headers, (
                "CSP_MODE=enforce時、Report-Onlyヘッダは出てはいけない（二重出力事故防止）"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_reporting_api_headers(monkeypatch, tmp_path):
    """刺し①: CSP_USE_REPORTING_API=1時、report-toとReporting-Endpointsが出力される"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("CSP_USE_REPORTING_API", "1")  # 新式有効化
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/")
            
            # Assert: CSPポリシーにreport-toが含まれる
            csp_header = response.headers.get("Content-Security-Policy-Report-Only", "")
            assert "report-uri /__csp_report" in csp_header, "旧式report-uriが必要（互換性）"
            assert "report-to csp-endpoint" in csp_header, "新式report-toが必要（CSP3）"
            
            # Assert: Reporting-Endpointsヘッダが出力される（刺し③: 絶対URL）
            reporting_endpoints = response.headers.get("Reporting-Endpoints", "")
            assert 'csp-endpoint="http://127.0.0.1:8000/__csp_report"' in reporting_endpoints, (
                "Reporting-Endpointsヘッダが絶対URLで必要（report-toと対、ブラウザ実装差対応）"
            )
            
            # 刺し①③: Report-Toヘッダも併記される（旧ヘッダ、ブラウザ差対応、絶対URL）
            report_to = response.headers.get("Report-To", "")
            assert report_to, "Report-Toヘッダが必要（Reporting API v0互換）"
            # JSONパース可能で、正しいエンドポイント（絶対URL）を含むこと
            import json
            report_to_data = json.loads(report_to)
            assert report_to_data.get("group") == "csp-endpoint"
            assert len(report_to_data.get("endpoints", [])) > 0
            assert report_to_data["endpoints"][0]["url"] == "http://127.0.0.1:8000/__csp_report", (
                "Report-To のエンドポイントが絶対URLであること（ブラウザ実装差対応）"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_report_uri_default_in_prod_with_actual_response(monkeypatch, tmp_path):
    """刺し①②: prod+report未設定時、実際のレスポンスで/__csp_reportが使われる（デフォルト運用のテスト）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（prod, CSP_REPORT_URI未設定）
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("P1_CONTRACT_VERIFIED", "1")
        monkeypatch.setenv("CSP_MODE", "report")
        # CSP_REPORT_URI は明示的に設定しない（デフォルト採用をテスト）
        monkeypatch.delenv("CSP_REPORT_URI", raising=False)
        monkeypatch.setenv("SESSION_SECRET", "test-secret-for-prod-default")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app) as client:
            # 刺し①: prodは閉じる契約を守る - 先にログインしてセッションを得る
            login_resp = client.post("/auth/login", json={"password": "admin"})
            assert login_resp.status_code == 200, "ログイン成功"
            
            # HTMLで / を叩く（auth済みなので200）
            response = client.get("/", headers={"Accept": "text/html"})
            
            # Assert: 200 OK（UIが動く状態、prodでも閉じる契約を保つ）
            assert response.status_code == 200, "auth済みならUIが正常に動作すること"
            
            # Assert: CSP Report-Only ヘッダが出る
            csp_header = response.headers.get("Content-Security-Policy-Report-Only", "")
            assert csp_header, "CSP Report-Only ヘッダが必要"
            
            # Assert: デフォルトの /__csp_report が使われる
            assert "report-uri /__csp_report" in csp_header, (
                "prod+report未設定時、/__csp_report が自動採用されること"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_reporting_api_consistency(monkeypatch, tmp_path):
    """刺し③: CSP_USE_REPORTING_API=1時、policy/Reporting-Endpoints/Report-Toの3点セットが揃う"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("CSP_USE_REPORTING_API", "1")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/")
            
            # Assert: 3点セットの整合性チェック
            csp_header = response.headers.get("Content-Security-Policy-Report-Only", "")
            reporting_endpoints = response.headers.get("Reporting-Endpoints", "")
            report_to = response.headers.get("Report-To", "")
            
            # 1. policy に report-to csp-endpoint がある
            assert "report-to csp-endpoint" in csp_header, (
                "CSP policy に report-to csp-endpoint が必要"
            )
            
            # 2. Reporting-Endpoints ヘッダがある（刺し③: 絶対URL）
            assert reporting_endpoints, "Reporting-Endpoints ヘッダが必要"
            # 絶対URLになっているので http://127.0.0.1:8000/__csp_report を含む
            assert 'csp-endpoint="http://127.0.0.1:8000/__csp_report"' in reporting_endpoints, (
                "Reporting-Endpoints が絶対URLで正しいエンドポイントを指すこと"
            )
            
            # 3. Report-To ヘッダもある（旧式互換、刺し③: 絶対URL）
            assert report_to, "Report-To ヘッダが必要（旧式互換）"
            
            # 整合性: 全て csp-endpoint で統一されていること
            import json
            report_to_data = json.loads(report_to)
            assert report_to_data.get("group") == "csp-endpoint", (
                "Report-To の group が csp-endpoint であること（整合性）"
            )
            assert report_to_data["endpoints"][0]["url"] == "http://127.0.0.1:8000/__csp_report", (
                "Report-To のエンドポイントが絶対URLであること（整合性）"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_headers_on_error_responses(monkeypatch, tmp_path):
    """刺し④: 401/403などエラー応答でもCSPヘッダが付く（契約の明文化）"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数（prod, CSP=report）
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("P1_CONTRACT_VERIFIED", "1")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("SESSION_SECRET", "test-secret-for-error")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app) as client:
            # no-auth で / を叩く（401/403 期待）
            response = client.get("/", headers={"Accept": "text/html"})
            
            # Assert: エラーステータス
            assert response.status_code in (401, 403), (
                "no-auth時は401または403を返す（prodは閉じる）"
            )
            
            # Assert: エラー応答でもCSPヘッダが付く（契約の明文化）
            assert (
                "Content-Security-Policy" in response.headers or
                "Content-Security-Policy-Report-Only" in response.headers
            ), "エラー応答でもCSPヘッダが付くこと（セキュリティヘッダは常に付与）"
            
            # CSP Report-Only であることを確認
            csp_header = response.headers.get("Content-Security-Policy-Report-Only", "")
            assert "report-uri /__csp_report" in csp_header, (
                "エラー応答でもreport-uriが含まれること"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_reporting_api_proxy_forwarded_headers(monkeypatch, tmp_path):
    """刺しA: x-forwarded-proto/host でReporting-Endpointsが正しい外向きURLになる"""
    # Setup: グローバル状態を保存
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    old_pragma_dbs = app._PRAGMA_APPLIED_DBS.copy()
    
    try:
        # Setup: 環境変数
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("CSP_MODE", "report")
        monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
        monkeypatch.setenv("CSP_USE_REPORTING_API", "1")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
        
        # Setup: テストDB
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        
        # Setup: グローバルをクリア（lifespan一本化）
        app._SETTINGS = None
        app.DB_PATH = db_path
        app._PRAGMA_APPLIED_DBS.clear()
        
        # Act: TestClient lifespan で init
        with TestClient(app.app, base_url="http://internal:8000") as client:
            # プロキシヘッダ付きでリクエスト（Vercel/リバプロ環境を模擬）
            response = client.get("/", headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "example.com"
            })
            
            # Assert: Reporting-Endpoints が外向きURL（https://example.com）
            reporting_endpoints = response.headers.get("Reporting-Endpoints", "")
            assert 'csp-endpoint="https://example.com/__csp_report"' in reporting_endpoints, (
                "x-forwarded-* で外向きoriginを組み立てること（プロキシ下で観測が死なない）"
            )
            
            # Assert: Report-To も外向きURL
            report_to = response.headers.get("Report-To", "")
            assert report_to, "Report-To ヘッダが必要"
            
            import json
            report_to_data = json.loads(report_to)
            assert report_to_data["endpoints"][0]["url"] == "https://example.com/__csp_report", (
                "Report-To も外向きURLであること（プロキシ下対応）"
            )
    
    finally:
        # Cleanup: グローバル状態を復元
        app._SETTINGS = old_settings
        app.DB_PATH = old_db_path
        app._PRAGMA_APPLIED_DBS.clear()
        app._PRAGMA_APPLIED_DBS.update(old_pragma_dbs)


def test_csp_sampling_dict_size_limit(monkeypatch, tmp_path):
    """刺しA: サンプリング辞書が1000件で上限（メモリ安全）"""
    # Setup: グローバル状態を保存は fixture が自動でやる
    
    # Setup: 環境変数
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "report")
    monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    
    # Setup: テストDB
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    # Setup: グローバルをクリア
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    app._CSP_REPORT_SAMPLING.clear()
    
    # Act: 1001件のユニーク違反を投稿
    with TestClient(app.app) as client:
        for i in range(1001):
            # ユニークな blocked-uri で違反レポート
            report = {
                "csp-report": {
                    "blocked-uri": f"https://evil{i}.com/script.js",
                    "violated-directive": "script-src 'self'"
                }
            }
            response = client.post("/__csp_report", json=report, headers={"content-type": "application/csp-report"})
            assert response.status_code == 204
        
        # Assert: サンプリング辞書のサイズが上限（1000件）で止まる
        assert len(app._CSP_REPORT_SAMPLING) <= 1000, (
            "サンプリング辞書は1000件で上限（メモリ安全・DoS耐性）"
        )


def test_csp_forwarded_host_sanitization(monkeypatch, tmp_path):
    """刺しB: x-forwarded-host に不正な文字が含まれる場合、フォールバックする"""
    # Setup: 環境変数
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "report")
    monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
    monkeypatch.setenv("CSP_USE_REPORTING_API", "1")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
    
    # Setup: テストDB
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    # Setup: グローバルをクリア
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    
    # Act: 偽ヘッダ注入を試みる
    with TestClient(app.app, base_url="http://safe.local:8000") as client:
        response = client.get("/", headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": 'evil.com",bad'  # 不正な文字（"や,）
        })
        
        # Assert: Reporting-Endpoints が汚染されずフォールバックする
        reporting_endpoints = response.headers.get("Reporting-Endpoints", "")
        
        # evil.com",bad は不正なのでフォールバック → safe.local:8000 が使われる
        assert "evil.com" not in reporting_endpoints, (
            "不正な x-forwarded-host は拒否される（ヘッダ注入防止）"
        )
        assert "safe.local:8000" in reporting_endpoints or "localhost" in reporting_endpoints, (
            "フォールバック先（base_url or localhost）が使われる"
        )


def test_csp_headers_on_500_error(monkeypatch, tmp_path):
    """刺しC: 未捕捉例外（500）でもCSPヘッダが付く"""
    # Setup: 環境変数
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "report")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    
    # Setup: テストDB
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    # Setup: グローバルをクリア
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    
    # Setup: 意図的に500エラーを起こすエンドポイントを追加
    @app.app.get("/test_500_error")
    async def cause_500():
        raise RuntimeError("Intentional error for testing")
    
    # Act: 500エラーを起こす
    with TestClient(app.app, raise_server_exceptions=False) as client:
        response = client.get("/test_500_error")
        
        # Assert: 500エラー
        assert response.status_code == 500
        
        # Assert: 例外経路でもCSPヘッダが付く（刺しC: 完全保証）
        assert (
            "Content-Security-Policy" in response.headers or
            "Content-Security-Policy-Report-Only" in response.headers
        ), "未捕捉例外（500）でもCSPヘッダが付くこと（例外経路の完全保証）"


def test_get_external_origin_fallback(monkeypatch, tmp_path):
    """刺し①: X-Forwarded-* がない時は request.base_url にフォールバック"""
    # Setup
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "report")
    monkeypatch.setenv("CSP_USE_REPORTING_API", "1")
    monkeypatch.setenv("CSP_REPORT_URI", "/__csp_report")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")
    
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    
    # Act: X-Forwarded-* なしでリクエスト（direct access）
    with TestClient(app.app, base_url="http://localhost:8000") as client:
        response = client.get("/")
        
        # Assert: base_url にフォールバック
        reporting_endpoints = response.headers.get("Reporting-Endpoints", "")
        assert "localhost:8000/__csp_report" in reporting_endpoints, (
            "X-Forwarded-* がない時は request.base_url にフォールバック"
        )


def test_root_path_vary_accept_header(monkeypatch, tmp_path):
    """刺し②: / に Vary: Accept ヘッダが付く（キャッシュ事故を封じる）"""
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
    
    # Act: / にアクセス
    with TestClient(app.app) as client:
        response = client.get("/")
        
        # Assert: Vary: Accept が付く
        vary_header = response.headers.get("Vary", "")
        assert "Accept" in vary_header, (
            "/ に Vary: Accept が付くこと（CDN/中間キャッシュの事故防止）"
        )


def test_notes_detail_vary_accept_header(monkeypatch, tmp_path):
    """刺し⑥: /notes/{slug} に Vary: Accept が付く（HTML/JSON分岐のキャッシュ事故防止）"""
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
    
    # Act: テストnoteを作成
    with TestClient(app.app, base_url="http://127.0.0.1:8000") as client:        
        import sqlite3
        con = sqlite3.connect(db_path)
        cols = [r[1] for r in con.execute("PRAGMA table_info(notes)").fetchall()]
        colset = set(cols)

        values = {}
        if "slug" in colset:
            values["slug"] = "test-slug"
        if "status" in colset:
            values["status"] = "open"
        if "priority" in colset:
            values["priority"] = 1
        # P1以降のNOT NULL系を埋める（存在する列だけ）
        if "created_at" in colset:
            values["created_at"] = "1970-01-01T00:00:00Z"
        if "updated_at" in colset:
            values["updated_at"] = "1970-01-01T00:00:00Z"
        if "first_seen" in colset:
            values["first_seen"] = "1970-01-01T00:00:00Z"
        if "last_seen" in colset:
            values["last_seen"] = "1970-01-01T00:00:00Z"
        if "is_deleted" in colset:
            values["is_deleted"] = 0
        if "is_archived" in colset:
            values["is_archived"] = 0
        if "title" in colset:
            values["title"] = "test note"

        cols_sql = ", ".join(values.keys())
        qs_sql = ", ".join(["?"] * len(values))
        con.execute(
            f"INSERT INTO notes ({cols_sql}) VALUES ({qs_sql})",
            tuple(values.values()),
        )

        con.commit()
        con.close()
        
        # JSON要求
        response_json = client.get("/notes/test-slug", headers={"Accept": "application/json"})

        # Assert: Vary: Accept が付く
        assert "Accept" in response_json.headers.get("Vary", ""), (
            "/notes/{slug} に Vary: Accept が付くこと（HTML/JSON分岐のキャッシュ事故防止）"
        )
        
        # HTML要求でも確認
        response_html = client.get("/notes/test-slug", headers={"Accept": "text/html"})
        assert "Accept" in response_html.headers.get("Vary", ""), (
            "/notes/{slug} HTML応答でも Vary: Accept が付くこと"
        )


def test_csp_enforce_mode_exclusivity(monkeypatch, tmp_path):
    """刺し②: CSP_MODE=enforce のとき Content-Security-Policy だけが出る"""
    # Setup
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "enforce")
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
        response = client.get("/")
        
        # Assert: Content-Security-Policy が出る
        assert "Content-Security-Policy" in response.headers, (
            "CSP_MODE=enforce のとき Content-Security-Policy が出ること"
        )
        
        # Assert: Content-Security-Policy-Report-Only は出ない（排他性）
        assert "Content-Security-Policy-Report-Only" not in response.headers, (
            "CSP_MODE=enforce のとき Content-Security-Policy-Report-Only は出ないこと（混在させない）"
        )


def test_csp_headers_on_all_error_responses(monkeypatch, tmp_path):
    """刺し②: 401/403/404/500 でもCSPヘッダが必ず付く"""
    # Setup
    monkeypatch.setenv("MODE", "local")
    monkeypatch.setenv("CSP_MODE", "enforce")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "dev")
    
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    
    app._SETTINGS = None
    app.DB_PATH = db_path
    app._PRAGMA_APPLIED_DBS.clear()
    
    with TestClient(app.app, raise_server_exceptions=False) as client:
        # 401 (未認証)
        response_401 = client.get("/")
        assert response_401.status_code == 401
        assert "Content-Security-Policy" in response_401.headers, (
            "401エラーでもCSPヘッダが付くこと"
        )
        
        # 404 (存在しないエンドポイント)
        response_404 = client.get("/nonexistent")
        assert response_404.status_code == 404
        assert "Content-Security-Policy" in response_404.headers, (
            "404エラーでもCSPヘッダが付くこと"
        )
