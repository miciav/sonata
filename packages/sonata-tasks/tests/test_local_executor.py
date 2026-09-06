from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from sonata_tasks.errors import CommandTimeoutError, UnsupportedCommandOptionError
from sonata_tasks.execution.local import LocalCommandTaskExecutor
from sonata_tasks.execution.models import CommandOptions, CommandTaskSpec


def test_local_executor_applies_cwd_env_and_collects_both_streams(tmp_path: Path) -> None:
    spec = CommandTaskSpec(
        "x",
        "X",
        (
            sys.executable,
            "-c",
            "import os,sys;print(os.getcwd());print(os.environ['MARK']);"
            "print('err',file=sys.stderr)",
        ),
        role="builder",
        options=CommandOptions(cwd=tmp_path, env={"MARK": "present"}),
    )
    result = LocalCommandTaskExecutor(target_key="local-ci").run(spec)
    assert result.ok
    assert result.stdout.splitlines() == [str(tmp_path), "present"]
    assert result.stderr.strip() == "err"


def test_local_executor_honours_exit_codes_and_dry_run() -> None:
    executor = LocalCommandTaskExecutor()
    spec = CommandTaskSpec(
        "x",
        "X",
        (sys.executable, "-c", "raise SystemExit(17)"),
        options=CommandOptions(expected_exit_codes=frozenset({17})),
    )
    assert executor.run(spec).ok
    dry = executor.run(
        CommandTaskSpec("dry", "Dry", (sys.executable, "-c", "raise SystemExit(9)")),
        dry_run=True,
    )
    assert dry.ok


def test_local_executor_rejects_remote_dir_before_spawn() -> None:
    marker = Path("/tmp/sonata-local-executor-must-not-exist")
    spec = CommandTaskSpec(
        "x",
        "X",
        (sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"),
        options=CommandOptions(remote_dir="/srv/app"),
    )
    with pytest.raises(UnsupportedCommandOptionError):
        LocalCommandTaskExecutor().run(spec)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_timeout_stops_the_process_group_and_preserves_output() -> None:
    spec = CommandTaskSpec(
        "x",
        "X",
        (
            sys.executable,
            "-c",
            "import os,time;print(os.getpid(), flush=True);"
            "print('started', flush=True);time.sleep(30)",
        ),
        options=CommandOptions(timeout_seconds=0.2),
    )
    with pytest.raises(CommandTimeoutError) as raised:
        LocalCommandTaskExecutor().run(spec)
    pid = int(raised.value.stdout.splitlines()[0])
    assert "started" in raised.value.stdout
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
