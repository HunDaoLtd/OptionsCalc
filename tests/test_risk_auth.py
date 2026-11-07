from __future__ import annotations

import sqlite3
import os
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from server.app import create_app
from server.manage_risk_auth import _load_values, list_sessions, set_password
from server.risk_auth import (
    COOKIE_MAX_AGE_SECONDS,
    PASSWORD_HASH_ENV,
    SESSION_DB_ENV,
    SESSION_GENERATION_ENV,
    RiskAuthConfig,
    RiskAuthenticationRequired,
    RiskAuthManager,
    RiskAuthNotConfigured,
    RiskAuthRateLimited,
)
from tests.test_risk_service import make_service, valid_payload
from tests.conftest import mirror_proxy_cookie


def login(client, password: str, *, origin="http://localhost"):
    return client.post(
        "/api/options/liquidation-risk/auth/login",
        json={"password": password},
        headers={"Origin": origin, "User-Agent": "Risk Test Browser"},
    )


def test_missing_or_invalid_auth_configuration_fails_closed(tmp_path):
    missing = RiskAuthManager.from_environment({})
    with pytest.raises(RiskAuthNotConfigured):
        missing.validate_session(None, client_ip="127.0.0.1", user_agent="test")

    invalid = RiskAuthManager.from_environment(
        {
            PASSWORD_HASH_ENV: "not-an-argon-hash",
            SESSION_GENERATION_ENV: "g" * 64,
            SESSION_DB_ENV: str(tmp_path / "invalid.sqlite3"),
        }
    )
    with pytest.raises(RiskAuthNotConfigured):
        invalid.validate_session("token", client_ip="127.0.0.1", user_agent="test")

    client = create_app(make_service(), missing).test_client()
    accounts = client.get("/api/options/liquidation-risk/accounts")
    assert accounts.status_code == 503
    assert accounts.get_json() == {"error": {"code": "auth_not_configured"}}
    assert "WWW-Authenticate" not in accounts.headers
    assert client.get("/api/health").status_code == 200


def test_login_sets_long_lived_cookie_and_stores_only_token_digest(risk_auth):
    client = create_app(make_service(), risk_auth.manager).test_client()
    response = login(client, risk_auth.password)

    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert "options_calc_risk_session_dev=" in cookie
    assert f"Max-Age={COOKIE_MAX_AGE_SECONDS}" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/2_options_calc/risk/" in cookie
    assert "Secure" not in cookie

    sessions = risk_auth.manager.list_sessions()
    assert len(sessions) == 1
    database = risk_auth.manager.config.session_db
    with sqlite3.connect(database) as connection:
        token_hash = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert len(token_hash) == 64
        assert "correct horse" not in database.read_bytes().decode("latin1")
        assert "options_calc_risk_session_dev" not in database.read_bytes().decode(
            "latin1"
        )
    if os.name != "nt":
        assert database.stat().st_mode & 0o777 == 0o600


def test_production_cookie_is_secure_and_session_can_be_revoked(tmp_path):
    password = "a production strength password"
    config = RiskAuthConfig(
        password_hash=PasswordHasher().hash(password),
        session_generation="b" * 64,
        session_db=tmp_path / "secure.sqlite3",
        cookie_secure=True,
    )
    manager = RiskAuthManager(config)
    client = create_app(make_service(), manager).test_client()
    response = login(client, password)
    assert "__Secure-options_calc_risk_session=" in response.headers["Set-Cookie"]
    assert "Secure" in response.headers["Set-Cookie"]
    mirror_proxy_cookie(client, response)

    accounts = client.get("/api/options/liquidation-risk/accounts")
    assert accounts.status_code == 200
    assert f"Max-Age={COOKIE_MAX_AGE_SECONDS}" in accounts.headers["Set-Cookie"]
    session_id = manager.list_sessions()[0]["session_id"]
    assert manager.revoke_session(str(session_id)) is True
    denied = client.get("/api/options/liquidation-risk/accounts")
    assert denied.status_code == 401
    assert "WWW-Authenticate" not in denied.headers


def test_login_rate_limit_persists_failures(risk_auth):
    for attempt in range(4):
        with pytest.raises(RiskAuthenticationRequired):
            risk_auth.manager.authenticate(
                "wrong password",
                client_ip="203.0.113.7",
                user_agent=f"attempt-{attempt}",
            )
    with pytest.raises(RiskAuthRateLimited) as limited:
        risk_auth.manager.authenticate(
            "wrong password", client_ip="203.0.113.7", user_agent="attempt-5"
        )
    assert limited.value.retry_after_seconds == 900
    with pytest.raises(RiskAuthRateLimited):
        risk_auth.manager.authenticate(
            risk_auth.password, client_ip="203.0.113.7", user_agent="blocked"
        )


def test_session_logout_origin_and_authorize_contract(risk_auth):
    app = create_app(make_service(), risk_auth.manager)
    client = app.test_client()
    rejected = login(client, risk_auth.password, origin="https://example.test")
    assert rejected.status_code == 403
    assert rejected.get_json() == {"error": {"code": "origin_not_allowed"}}

    response = login(client, risk_auth.password)
    assert response.status_code == 200
    mirror_proxy_cookie(client, response)
    assert client.get("/api/options/liquidation-risk/auth/authorize").status_code == 204
    evaluation = client.post(
        "/api/options/liquidation-risk/evaluate",
        json=valid_payload(),
        headers={"Origin": "http://localhost"},
    )
    assert evaluation.status_code == 200
    logout = client.post(
        "/api/options/liquidation-risk/auth/logout",
        json={},
        headers={"Origin": "http://localhost"},
    )
    assert logout.status_code == 204
    assert client.get("/api/options/liquidation-risk/auth/authorize").status_code == 401


def test_password_command_writes_only_hash_and_revokes_existing_sessions(
    tmp_path, monkeypatch, capsys
):
    env_file = tmp_path / "options-calc.env"
    database = tmp_path / "risk-auth.sqlite3"
    env_file.write_text(
        f"BINANCE_OPTIONS_ACCOUNT_IDS=main\n{SESSION_DB_ENV}={database}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    answers = iter(["new shared password", "new shared password"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(answers))

    assert set_password(env_file, tmp_path / "backups") == 0
    output = capsys.readouterr().out
    values = _load_values(env_file)
    assert "new shared password" not in env_file.read_text(encoding="utf-8")
    assert "new shared password" not in output
    assert values[PASSWORD_HASH_ENV].startswith("$argon2id$")
    assert len(values[SESSION_GENERATION_ENV]) == 64
    assert Path(values[SESSION_DB_ENV]) == database
    assert len(list((tmp_path / "backups").glob("*.bak"))) == 1


def test_credential_generation_invalidates_sessions_created_by_an_old_process(tmp_path):
    password = "credential generation password"
    database = tmp_path / "generation.sqlite3"
    old_manager = RiskAuthManager(
        RiskAuthConfig(
            password_hash=PasswordHasher().hash(password),
            session_generation="old-" + "a" * 60,
            session_db=database,
            cookie_secure=False,
        )
    )
    session = old_manager.authenticate(
        password, client_ip="192.0.2.10", user_agent="Old worker"
    )
    new_manager = RiskAuthManager(
        RiskAuthConfig(
            password_hash=PasswordHasher().hash(password),
            session_generation="new-" + "b" * 60,
            session_db=database,
            cookie_secure=False,
        )
    )

    with pytest.raises(RiskAuthenticationRequired):
        new_manager.validate_session(
            session.token, client_ip="192.0.2.10", user_agent="Old worker"
        )
    assert new_manager.list_sessions() == []


def test_database_failure_returns_503_without_exposing_details(tmp_path):
    database_directory = tmp_path / "database-is-a-directory"
    database_directory.mkdir()
    manager = RiskAuthManager(
        RiskAuthConfig(
            password_hash=PasswordHasher().hash("database failure password"),
            session_generation="c" * 64,
            session_db=database_directory,
            cookie_secure=False,
        )
    )
    client = create_app(make_service(), manager).test_client()
    client.set_cookie("options_calc_risk_session_dev", "unreadable-store", path="/")

    response = client.get("/api/options/liquidation-risk/auth/session")
    assert response.status_code == 503
    assert response.get_json() == {"error": {"code": "auth_unavailable"}}
    assert str(database_directory) not in response.get_data(as_text=True)
    assert "WWW-Authenticate" not in response.headers


def test_session_listing_masks_ip_and_truncates_device_text(risk_auth, tmp_path, capsys):
    risk_auth.manager.authenticate(
        risk_auth.password,
        client_ip="203.0.113.42",
        user_agent="Browser/1.0 " + "device-detail-" * 20,
    )
    env_file = tmp_path / "options-calc.env"
    env_file.write_text(
        f"{PASSWORD_HASH_ENV}='{risk_auth.manager.config.password_hash}'\n"
        f"{SESSION_GENERATION_ENV}={risk_auth.manager.config.session_generation}\n"
        f"{SESSION_DB_ENV}='{risk_auth.manager.config.session_db}'\n",
        encoding="utf-8",
    )

    assert list_sessions(env_file) == 0
    output = capsys.readouterr().out
    assert "203.0.113.x" in output
    assert "203.0.113.42" not in output
    assert len(output.split("agent=", 1)[1].strip()) <= 81
