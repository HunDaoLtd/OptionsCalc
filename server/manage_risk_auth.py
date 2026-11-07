from __future__ import annotations

import argparse
import getpass
import ipaddress
import os
import secrets
import shlex
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from dotenv import dotenv_values

try:
    from .risk_auth import (
        COOKIE_SECURE_ENV,
        DEFAULT_SESSION_DB,
        PASSWORD_HASH_ENV,
        SESSION_DB_ENV,
        SESSION_GENERATION_ENV,
        RiskAuthManager,
        config_from_environment,
    )
except ImportError:
    from risk_auth import (
        COOKIE_SECURE_ENV,
        DEFAULT_SESSION_DB,
        PASSWORD_HASH_ENV,
        SESSION_DB_ENV,
        SESSION_GENERATION_ENV,
        RiskAuthManager,
        config_from_environment,
    )


DEFAULT_ENV_FILE = Path("/etc/options-calc.env")
DEFAULT_BACKUP_DIR = Path("/var/backups/options-calc")


def _require_root(env_file: Path) -> None:
    if env_file == DEFAULT_ENV_FILE and hasattr(os, "geteuid") and os.geteuid() != 0:
        raise PermissionError("Production risk auth management must run as root")


def _load_values(env_file: Path) -> dict[str, str]:
    if not env_file.is_file():
        raise FileNotFoundError(f"Environment file does not exist: {env_file}")
    return {
        key: value
        for key, value in dotenv_values(env_file).items()
        if value is not None
    }


def _replace_values(text: str, replacements: dict[str, str]) -> str:
    remaining = dict(replacements)
    output: list[str] = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            output.append(f"{key}={shlex.quote(remaining.pop(key))}")
        else:
            output.append(line)
    if output and output[-1]:
        output.append("")
    output.extend(f"{key}={shlex.quote(value)}" for key, value in remaining.items())
    return "\n".join(output) + "\n"


def _atomic_update_environment(
    env_file: Path, replacements: dict[str, str], backup_dir: Path
) -> Path:
    _require_root(env_file)
    original = env_file.read_text(encoding="utf-8")
    metadata = env_file.stat()
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"options-calc.env.{timestamp}.bak"
    shutil.copy2(env_file, backup)
    os.chmod(backup, 0o600)

    replacement = _replace_values(original, replacements)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".options-calc.env.", dir=env_file.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        if hasattr(os, "chown"):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, env_file)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def _manager_from_file(env_file: Path) -> RiskAuthManager:
    values = _load_values(env_file)
    return RiskAuthManager(config_from_environment(values))


def _masked_ip(value: object) -> str:
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return "unknown"
    if address.version == 4:
        parts = str(address).split(".")
        return ".".join((*parts[:3], "x"))
    return f"{address.exploded[:19]}:…"


def _masked_agent(value: object) -> str:
    agent = str(value).replace("\r", " ").replace("\n", " ")
    return agent[:80] + ("…" if len(agent) > 80 else "")


def set_password(env_file: Path, backup_dir: Path) -> int:
    _require_root(env_file)
    password = getpass.getpass("New Risk access password: ")
    confirmation = getpass.getpass("Confirm Risk access password: ")
    if password != confirmation:
        raise ValueError("Password confirmation does not match")
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12 to 256 characters")

    hasher = PasswordHasher(
        time_cost=2,
        memory_cost=19_456,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )
    values = _load_values(env_file)
    database = values.get(SESSION_DB_ENV, DEFAULT_SESSION_DB)
    replacements = {
        PASSWORD_HASH_ENV: hasher.hash(password),
        SESSION_GENERATION_ENV: secrets.token_hex(32),
        SESSION_DB_ENV: database,
        COOKIE_SECURE_ENV: "true",
    }
    backup = _atomic_update_environment(env_file, replacements, backup_dir)
    manager = _manager_from_file(env_file)
    manager.initialize_store()
    revoked = manager.revoke_all()
    print(f"Risk password hash updated; revoked sessions: {revoked}")
    print(f"Environment backup: {backup}")
    print("Restart the application service to load the authentication changes.")
    return 0


def list_sessions(env_file: Path) -> int:
    sessions = _manager_from_file(env_file).list_sessions()
    if not sessions:
        print("No active Risk sessions.")
        return 0
    for session in sessions:
        print(
            f"{session['session_id']} created={session['created_at']} "
            f"last_seen={session['last_seen_at']} ip={_masked_ip(session['last_ip'])} "
            f"agent={_masked_agent(session['user_agent'])}"
        )
    return 0


def revoke_session(env_file: Path, session_id: str) -> int:
    revoked = _manager_from_file(env_file).revoke_session(session_id)
    print("Session revoked." if revoked else "Session not found.")
    return 0 if revoked else 1


def revoke_all(env_file: Path) -> int:
    count = _manager_from_file(env_file).revoke_all()
    print(f"Revoked Risk sessions: {count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Options Calc Risk access")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("set-password")
    subparsers.add_parser("sessions")
    revoke_parser = subparsers.add_parser("revoke")
    revoke_parser.add_argument("session_id")
    subparsers.add_parser("revoke-all")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        if arguments.command == "set-password":
            return set_password(arguments.env_file, arguments.backup_dir)
        if arguments.command == "sessions":
            return list_sessions(arguments.env_file)
        if arguments.command == "revoke":
            return revoke_session(arguments.env_file, arguments.session_id)
        if arguments.command == "revoke-all":
            return revoke_all(arguments.env_file)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Risk auth management failed: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
