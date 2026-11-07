from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie

import pytest
from argon2 import PasswordHasher

from server.risk_auth import RiskAuthConfig, RiskAuthManager


@dataclass(frozen=True)
class RiskAuthFixture:
    manager: RiskAuthManager
    password: str


def mirror_proxy_cookie(client, response) -> None:
    """Make Flask's upstream-only test URL receive the public-path cookie."""
    cookie = SimpleCookie()
    cookie.load(response.headers["Set-Cookie"])
    name, morsel = next(iter(cookie.items()))
    client.set_cookie(name, morsel.value, path="/")


@pytest.fixture
def risk_auth(tmp_path):
    password = "correct horse battery staple"
    hasher = PasswordHasher(
        time_cost=2,
        memory_cost=19_456,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )
    config = RiskAuthConfig(
        password_hash=hasher.hash(password),
        session_generation="a" * 64,
        session_db=tmp_path / "risk-auth.sqlite3",
        cookie_secure=False,
    )
    return RiskAuthFixture(RiskAuthManager(config), password)
