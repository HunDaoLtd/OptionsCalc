from __future__ import annotations

import os

from server.dev import load_local_environment


def test_load_local_environment_reads_file_without_overwriting_process_values(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env.local"
    env_path.write_text(
        "LOCAL_ONLY_VALUE=from-file\nPRESERVED_VALUE=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LOCAL_ONLY_VALUE", raising=False)
    monkeypatch.setenv("PRESERVED_VALUE", "from-process")

    load_local_environment(env_path)

    assert os.environ["LOCAL_ONLY_VALUE"] == "from-file"
    assert os.environ["PRESERVED_VALUE"] == "from-process"
