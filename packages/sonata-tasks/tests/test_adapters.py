from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from sonata_tasks.errors import UnsupportedCommandOptionError
from sonata_tasks.execution.adapters import HostCommandTaskExecutor, VmCommandTaskExecutor
from sonata_tasks.execution.models import CommandOptions, CommandTaskSpec


@dataclass(frozen=True)
class _Result:
    return_code: int = 0
    stdout: str = "ok"
    stderr: str = ""


class _Host:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def binding_key(self, role: str) -> str:
        return f"test:{role}"

    def run(
        self,
        command: list[str],
        /,
        *,
        cwd: Path | None,
        env: dict[str, str],
        dry_run: bool,
    ) -> _Result:
        self.calls.append((command, cwd, env, dry_run))
        return _Result()


class _Vm:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def run_vm_command(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str],
        remote_dir: str | None,
        dry_run: bool,
    ) -> _Result:
        self.calls.append((argv, env, remote_dir, dry_run))
        return _Result()


def test_host_adapter_forwards_supported_options() -> None:
    runner = _Host()
    executor = HostCommandTaskExecutor(runner, target_key="workstation")
    spec = CommandTaskSpec(
        "x",
        "X",
        ("echo", "ok"),
        role="builder",
        options=CommandOptions(cwd=Path("/repo"), env={"A": "B"}),
    )
    assert executor.run(spec, dry_run=True).ok
    assert runner.calls == [(["echo", "ok"], Path("/repo"), {"A": "B"}, True)]
    assert executor.binding_key("builder") == "workstation"


@pytest.mark.parametrize(
    "options",
    [CommandOptions(remote_dir="/srv/app"), CommandOptions(timeout_seconds=1)],
)
def test_host_adapter_rejects_unsupported_options_before_running(options: CommandOptions) -> None:
    runner = _Host()
    executor = HostCommandTaskExecutor(runner)
    with pytest.raises(UnsupportedCommandOptionError):
        executor.run(CommandTaskSpec("x", "X", ("true",), options=options))
    assert runner.calls == []


def test_vm_adapter_forwards_remote_options() -> None:
    runner = _Vm()
    executor = VmCommandTaskExecutor(runner, target_key="vm:builder")
    options = CommandOptions(env={"A": "B"}, remote_dir="/srv/app")
    spec = CommandTaskSpec("x", "X", ("make",), role="builder", options=options)
    assert executor.run(spec).ok
    assert runner.calls == [(("make",), {"A": "B"}, "/srv/app", False)]


@pytest.mark.parametrize(
    "options",
    [CommandOptions(cwd=Path("/repo")), CommandOptions(timeout_seconds=1)],
)
def test_vm_adapter_rejects_unsupported_options_before_running(options: CommandOptions) -> None:
    runner = _Vm()
    executor = VmCommandTaskExecutor(runner, target_key="vm:builder")
    with pytest.raises(UnsupportedCommandOptionError):
        executor.run(CommandTaskSpec("x", "X", ("true",), options=options))
    assert runner.calls == []
