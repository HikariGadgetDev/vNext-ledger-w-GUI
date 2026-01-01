# tests/test_prod_gate.py
from pathlib import Path
import os
import pytest
import app


def _restore_app_state(old_settings, old_db_path):
    # グローバルを元に戻す（他テストへ影響させない）
    if hasattr(app, "_SETTINGS"):
        app._SETTINGS = old_settings
    app.DB_PATH = old_db_path


def test_prod_gate_requires_ci_verification(monkeypatch, tmp_path):
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    try:
        # prod に必要な前提（load_settings が先に落ちないように）
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")

        # CIフラグ無し
        monkeypatch.delenv("P1_CONTRACT_VERIFIED", raising=False)

        # DBはテスト用tmpへ
        monkeypatch.setenv("DB_PATH", str(tmp_path / "ledger.sqlite3"))
        app.DB_PATH = Path(os.environ["DB_PATH"])

        # settings を作り直して gate を踏む
        if hasattr(app, "_SETTINGS"):
            app._SETTINGS = None
        app.init_settings()

        with pytest.raises(RuntimeError, match="P1 contract gate"):
            app.check_p1_contract_gate()
    finally:
        _restore_app_state(old_settings, old_db_path)


def test_prod_gate_allows_ci_verified(monkeypatch, tmp_path):
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    try:
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")

        # CIフラグあり
        monkeypatch.setenv("P1_CONTRACT_VERIFIED", "1")

        monkeypatch.setenv("DB_PATH", str(tmp_path / "ledger.sqlite3"))
        app.DB_PATH = Path(os.environ["DB_PATH"])


        if hasattr(app, "_SETTINGS"):
            app._SETTINGS = None
        app.init_settings()

        # 例外が出なければOK
        app.check_p1_contract_gate()
    finally:
        _restore_app_state(old_settings, old_db_path)


def test_prod_disallows_allow_local_json_noauth(monkeypatch, tmp_path):
    """安全弁: MODE=prod では ALLOW_LOCAL_JSON_NOAUTH を絶対に許可しない"""
    old_settings = getattr(app, "_SETTINGS", None)
    old_db_path = app.DB_PATH
    try:
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("SESSION_SECRET", "test-secret")

        # これを prod で許すと事故るので禁止（契約）
        monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1")

        monkeypatch.setenv("DB_PATH", str(tmp_path / "ledger.sqlite3"))
        app.DB_PATH = Path(os.environ["DB_PATH"])

        if hasattr(app, "_SETTINGS"):
            app._SETTINGS = None

        with pytest.raises(RuntimeError, match="ALLOW_LOCAL_JSON_NOAUTH must be disabled"):
            app.init_settings()
    finally:
        _restore_app_state(old_settings, old_db_path)