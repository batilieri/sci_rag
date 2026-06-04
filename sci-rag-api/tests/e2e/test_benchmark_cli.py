import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(os.getenv("RUN_E2E") != "1", reason="requires live indexed RAG stack")
def test_benchmark_cli_runs_against_live_api():
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark.py",
            "--gabarito",
            "tests/gabarito.json",
            "--api-url",
            os.getenv("RAG_API_URL", "http://127.0.0.1:8000"),
            "--api-key",
            os.environ["RAG_API_KEY"],
        ],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr
