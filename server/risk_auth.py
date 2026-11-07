from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


PASSWORD_HASH_ENV = "OPTIONS_CALC_RISK_PASSWORD_HASH"
SESSION_GENERATION_ENV = "OPTIONS_CALC_RISK_SESSION_GENERATION"
SESSION_DB_ENV = "OPTIONS_CALC_RISK_SESSION_DB"
COOKIE_SECURE_ENV = "OPTIONS_CALC_RISK_COOKIE_SECURE"
DEFAULT_SESSION_DB = "/var/lib/options-calc/risk-auth.sqlite3"
COOKIE_NAME = "__Secure-options_calc_risk_session"
DEV_COOKIE_NAME = "options_calc_risk_session_dev"
COOKIE_PATH = "/2_options_calc/risk/"
COOKIE_MAX_AGE_SECONDS = 34_560_000
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_FAILURE_LIMIT = 5
MAX_PASSWORD_LENGTH = 256


class RiskAuthError(RuntimeError):
    code = "auth_unavailable"
    status_code = 503
    retry_after_seconds: int | None = None


class RiskAuthNotConfigured(RiskAuthError):
    code = "auth_not_configured"


class RiskAuthenticationRequired(RiskAuthError):
    code = "authentication_required"
    status_code = 401


class RiskAuthRateLimited(RiskAuthError):
    code = "rate_limited"
    status_code = 429

    def __init__(self, retry_after_seconds: int):
        super().__init__("Too many failed login attempts")
        self.retry_after_seconds = max(1, retry_after_seconds)


class RiskAuthOriginRejected(RiskAuthError):
    code = "origin_not_allowed"
    status_code = 403


@dataclass(frozen=True)
class RiskAuthConfig:
    password_hash: str
    session_generation: str
    session_db: Path
    cookie_secure: bool = True

    @property
    def cookie_name(self) -> str:
        return COOKIE_NAME if self.cookie_secure else DEV_COOKIE_NAME


@dataclass(frozen=True)
class RiskSession:
    session_id: str
    token: str
    created_at: int
    last_seen_at: int
    user_agent: str
    last_ip: str


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def config_from_environment(
    environ: Mapping[str, str] | None = None,
) -> RiskAuthConfig:
    values = os.environ if environ is None else environ
    password_hash = values.get(PASSWORD_HASH_ENV, "").strip()
    generation = values.get(SESSION_GENERATION_ENV, "").strip()
    database = values.get(SESSION_DB_ENV, DEFAULT_SESSION_DB).strip()
    missing = [
        name
        for name, value in (
            (PASSWORD_HASH_ENV, password_hash),
            (SESSION_GENERATION_ENV, generation),
            (SESSION_DB_ENV, database),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"missing risk auth settings: {', '.join(missing)}")
    if len(generation) < 32 or len(generation) > 256:
        raise ValueError("invalid risk auth session generation")
    database_path = Path(database)
    if not database_path.is_absolute():
        raise ValueError("risk auth session database path must be absolute")
    return RiskAuthConfig(
        password_hash=password_hash,
        session_generation=generation,
        session_db=database_path,
        cookie_secure=_parse_bool(values.get(COOKIE_SECURE_ENV), default=True),
    )


class RiskAuthManager:
    def __init__(
        self,
        config: RiskAuthConfig | None,
        *,
        configuration_error: str | None = None,
        now_seconds: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        session_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self.config = config
        self.configuration_error = configuration_error
        self._now_seconds = now_seconds
        self._token_factory = token_factory
        self._session_id_factory = session_id_factory
        self._password_hasher = password_hasher or PasswordHasher(
            time_cost=2,
            memory_cost=19_456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        if self.config is not None:
            try:
                self._password_hasher.check_needs_rehash(self.config.password_hash)
            except InvalidHashError as error:
                self.configuration_error = "Risk authentication password hash is invalid"
                self.config = None
        self._schema_ready = False

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "RiskAuthManager":
        try:
            config = config_from_environment(environ)
        except ValueError as error:
            return cls(None, configuration_error=str(error))
        return cls(config)

    @property
    def cookie_name(self) -> str:
        return self._require_config().cookie_name

    @property
    def cookie_secure(self) -> bool:
        return self._require_config().cookie_secure

    def _require_config(self) -> RiskAuthConfig:
        if self.config is None:
            raise RiskAuthNotConfigured(
                self.configuration_error or "Risk authentication is not configured"
            )
        return self.config

    def _connect(self) -> sqlite3.Connection:
        config = self._require_config()
        try:
            config.session_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            connection = sqlite3.connect(config.session_db, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            os.chmod(config.session_db, 0o600)
            if not self._schema_ready:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL UNIQUE,
                        credential_generation TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL,
                        user_agent TEXT NOT NULL,
                        last_ip TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS login_failures (
                        client_ip TEXT NOT NULL,
                        attempted_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS login_failures_ip_time
                        ON login_failures(client_ip, attempted_at);
                    """
                )
                connection.commit()
                self._schema_ready = True
            return connection
        except (OSError, sqlite3.Error) as error:
            raise RiskAuthError("Risk authentication storage is unavailable") from error

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_metadata(value: str | None, limit: int) -> str:
        return (value or "unknown").replace("\r", " ").replace("\n", " ")[:limit]

    def initialize_store(self) -> None:
        connection = self._connect()
        connection.close()

    def authenticate(
        self,
        password: str,
        *,
        client_ip: str,
        user_agent: str | None,
    ) -> RiskSession:
        config = self._require_config()
        now = int(self._now_seconds())
        cutoff = now - LOGIN_FAILURE_WINDOW_SECONDS
        safe_ip = self._safe_metadata(client_ip, 64)
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM login_failures WHERE attempted_at < ?", (cutoff,)
            )
            failures = connection.execute(
                "SELECT attempted_at FROM login_failures "
                "WHERE client_ip = ? ORDER BY attempted_at",
                (safe_ip,),
            ).fetchall()
            connection.commit()
            if len(failures) >= LOGIN_FAILURE_LIMIT:
                retry_after = failures[0]["attempted_at"] + LOGIN_FAILURE_WINDOW_SECONDS - now
                raise RiskAuthRateLimited(retry_after)

            verified = False
            if isinstance(password, str) and len(password) <= MAX_PASSWORD_LENGTH:
                try:
                    verified = self._password_hasher.verify(
                        config.password_hash, password
                    )
                except VerifyMismatchError:
                    verified = False
                except (InvalidHashError, VerificationError) as error:
                    raise RiskAuthNotConfigured(
                        "Risk authentication password hash is invalid"
                    ) from error

            if not verified:
                connection.execute(
                    "INSERT INTO login_failures(client_ip, attempted_at) VALUES (?, ?)",
                    (safe_ip, now),
                )
                connection.commit()
                if len(failures) + 1 >= LOGIN_FAILURE_LIMIT:
                    raise RiskAuthRateLimited(LOGIN_FAILURE_WINDOW_SECONDS)
                raise RiskAuthenticationRequired("Invalid risk access password")

            connection.execute(
                "DELETE FROM login_failures WHERE client_ip = ?", (safe_ip,)
            )
            token = self._token_factory()
            session = RiskSession(
                session_id=self._session_id_factory(),
                token=token,
                created_at=now,
                last_seen_at=now,
                user_agent=self._safe_metadata(user_agent, 256),
                last_ip=safe_ip,
            )
            connection.execute(
                "INSERT INTO sessions("
                "session_id, token_hash, credential_generation, created_at, "
                "last_seen_at, user_agent, last_ip"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session.session_id,
                    self.token_hash(token),
                    config.session_generation,
                    now,
                    now,
                    session.user_agent,
                    safe_ip,
                ),
            )
            connection.commit()
            return session
        except RiskAuthError:
            raise
        except sqlite3.Error as error:
            raise RiskAuthError("Risk authentication storage is unavailable") from error
        finally:
            connection.close()

    def validate_session(
        self,
        token: str | None,
        *,
        client_ip: str,
        user_agent: str | None,
    ) -> RiskSession:
        config = self._require_config()
        if not token or len(token) > 512:
            raise RiskAuthenticationRequired("Risk session is missing")
        now = int(self._now_seconds())
        connection = self._connect()
        try:
            with connection:
                row = connection.execute(
                    "SELECT session_id, credential_generation, created_at, "
                    "last_seen_at, user_agent, last_ip FROM sessions WHERE token_hash = ?",
                    (self.token_hash(token),),
                ).fetchone()
                if row is None or not secrets.compare_digest(
                    row["credential_generation"], config.session_generation
                ):
                    raise RiskAuthenticationRequired("Risk session is invalid")
                safe_ip = self._safe_metadata(client_ip, 64)
                safe_agent = self._safe_metadata(user_agent, 256)
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ?, user_agent = ?, last_ip = ? "
                    "WHERE session_id = ?",
                    (now, safe_agent, safe_ip, row["session_id"]),
                )
                return RiskSession(
                    session_id=row["session_id"],
                    token=token,
                    created_at=row["created_at"],
                    last_seen_at=now,
                    user_agent=safe_agent,
                    last_ip=safe_ip,
                )
        except RiskAuthError:
            raise
        except sqlite3.Error as error:
            raise RiskAuthError("Risk authentication storage is unavailable") from error
        finally:
            connection.close()

    def logout(self, token: str | None) -> None:
        if not token:
            return
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(token),)
                )
        except sqlite3.Error as error:
            raise RiskAuthError("Risk authentication storage is unavailable") from error
        finally:
            connection.close()

    def list_sessions(self) -> list[dict[str, object]]:
        config = self._require_config()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT session_id, created_at, last_seen_at, user_agent, last_ip "
                "FROM sessions WHERE credential_generation = ? ORDER BY last_seen_at DESC",
                (config.session_generation,),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as error:
            raise RiskAuthError("Risk authentication storage is unavailable") from error
        finally:
            connection.close()

    def revoke_session(self, session_id: str) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM sessions WHERE session_id = ?", (session_id,)
                )
                return cursor.rowcount > 0
        except sqlite3.Error as error:
            raise RiskAuthError("Risk authentication storage is unavailable") from error
        finally:
            connection.close()

    def revoke_all(self) -> int:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute("DELETE FROM sessions")
                return cursor.rowcount
        except sqlite3.Error as error:
            raise RiskAuthError("Risk authentication storage is unavailable") from error
        finally:
            connection.close()
