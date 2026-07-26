"""Executes the shipped examples so an API-signature change can't silently
break them (nothing else under `tests/` touches `examples/`)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_demo_workflow_example_runs_successfully() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "examples" / "demo_workflow.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"examples/demo_workflow.py exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Compiled task IDs and outcomes" in result.stdout
