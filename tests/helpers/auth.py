# tests/helpers/auth.py
from __future__ import annotations

from fastapi.testclient import TestClient
import app


def make_test_session(role: str = "admin") -> str:
    # app.py の実装に合わせる（HMAC署名）
    # payload は最低 role があれば _ensure_role を通る
    return app._sign_session({"role": role})


class AuthClient(TestClient):
    def __init__(self, role: str = "admin", base_url: str = "http://127.0.0.1"):
        super().__init__(app.app, base_url=base_url)
        token = make_test_session(role)
        self.cookies.set("vnext_session", token, path="/")
