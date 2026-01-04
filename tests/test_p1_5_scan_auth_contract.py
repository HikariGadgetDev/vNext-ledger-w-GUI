import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_client(monkeypatch, tmp_path: Path, *, mode: str, allow_noauth: bool):
    # app.py は import-time に DB_PATH を読むので、reload 前に env をセット
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite3"))

    monkeypatch.setenv("MODE", mode)
    monkeypatch.setenv("ALLOW_LOCAL_JSON_NOAUTH", "1" if allow_noauth else "0")

    # settings が読むので最低限入れる
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)

    if mode == "prod":
        monkeypatch.setenv("P1_CONTRACT_VERIFIED", "1")
        monkeypatch.setenv("ADMIN_PASSWORD", "adminpw")
        monkeypatch.setenv("DEV_PASSWORD", "")
    else:
        monkeypatch.delenv("P1_CONTRACT_VERIFIED", raising=False)
        monkeypatch.setenv("ADMIN_PASSWORD", "")
        monkeypatch.setenv("DEV_PASSWORD", "")

    import app as appmod
    importlib.reload(appmod)

    # ★重要: lifespan を確実に動かす（Settings not loaded / app.state 未初期化を防ぐ）
    with TestClient(appmod.app) as client:
        # TestClient を返すのは with を抜けるので不可。
        # なので factory は "appmod" を返して、呼び出し側で with を使う。
        pass

    return appmod


def test_scan_prod_requires_auth(monkeypatch, tmp_path):
    appmod = _make_client(monkeypatch, tmp_path, mode="prod", allow_noauth=False)
    with TestClient(appmod.app) as c:
        r = c.post("/scan", headers={"Accept": "application/json"}, json={"root": str(tmp_path)})
        assert r.status_code in (401, 403)


def test_scan_local_allows_noauth_json_from_localhost(monkeypatch, tmp_path):
    appmod = _make_client(monkeypatch, tmp_path, mode="local", allow_noauth=True)
    with TestClient(appmod.app) as c:
        # TestClient の client.host は "testclient" になりがちなので、
        # app 側が local 判定でこれを許容している前提（後述の app.py 修正が必要）
        r = c.post("/scan", headers={"Accept": "application/json"}, json={"root": str(tmp_path)})
        assert r.status_code == 200


def test_scan_local_blocks_noauth_when_not_json(monkeypatch, tmp_path):
    appmod = _make_client(monkeypatch, tmp_path, mode="local", allow_noauth=True)
    with TestClient(appmod.app) as c:
        r = c.post("/scan", headers={"Accept": "text/html"}, json={"root": str(tmp_path)})
        assert r.status_code in (401, 403)
