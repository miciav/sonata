from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sonata_tasks.execution.models import CommandTaskSpec, TaskResult


class CommandTaskExecutor(Protocol):
    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult: ...

    def binding_key(self, role: str) -> str: ...


class CommandRunResult(Protocol):
    @property
    def return_code(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


class HostCommandRunner(Protocol):
    def run(
        self,
        command: list[str],
        /,
        *,
        cwd: Path | None,
        env: dict[str, str],
        dry_run: bool,
    ) -> CommandRunResult: ...


class VmCommandRunner(Protocol):
    def run_vm_command(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str],
        remote_dir: str | None,
        dry_run: bool,
    ) -> CommandRunResult: ...
