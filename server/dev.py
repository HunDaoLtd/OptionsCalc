from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_local_environment(env_path: Path | None = None) -> None:
    """Load local-only settings before Flask constructs its services."""
    load_dotenv(env_path or PROJECT_ROOT / ".env.local", override=False)


def main() -> None:
    load_local_environment()

    # Import only after .env.local is loaded: server.app creates the service at
    # module import time and reads account credentials from os.environ.
    from .app import app

    port = int(os.environ.get("PORT", "5004"))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
