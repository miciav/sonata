"""Executes the shipped examples so an API-signature change can't silently
break them (nothing else under `tests/` touches `examples/`)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_demo() -> subprocess.CompletedProcess[str]:
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
    return result


def test_demo_workflow_example_runs_successfully() -> None:
    assert "Compiled task IDs and outcomes" in _run_demo().stdout


def test_demo_workflow_example_reports_subtasks_without_compiling_them() -> None:
    """The demo is where the subtask contract is visible end to end: the steps
    appear in the event stream, and the compiled unit list is unaffected."""
    stdout = _run_demo().stdout
    events, _, compiled = stdout.partition("Compiled task IDs and outcomes:")

    assert "build-images/build/control-plane" in events
    assert "build-images/scan" in events
    assert "build-images/" not in compiled, (
        "a subtask id reached the compiled unit list:\n" + compiled
    )
    assert "002.build-images" in compiled
