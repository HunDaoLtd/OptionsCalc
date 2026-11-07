import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent


def test_legacy_gunicorn_app_entry_point_imports_from_server_directory():
    result = subprocess.run(
        [sys.executable, "-c", "import app; assert app.app is not None"],
        cwd=ROOT / "server",
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
