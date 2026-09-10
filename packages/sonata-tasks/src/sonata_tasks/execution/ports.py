"""Protocols for command executors and the runners they delegate to."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sonata_tasks.execution.models import CommandTaskSpec, TaskResult


class CommandTaskExecutor(Protocol):
    """Runs a resolved command task and reports the outcome."""

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        """Run ``task`` and return its result.

        ``dry_run`` asks for the outcome the executor would produce without
        performing the work.
        """
        ...

    def binding_key(self, role: str) -> str:
        """Return the non-empty key naming where ``role``'s commands run.

        The key is folded into the task fingerprint, so pointing a role at a
        different destination invalidates its recorded results.
        """
        ...


class CommandRunResult(Protocol):
    """The raw output of a runner invocation."""

    @property
    def return_code(self) -> int:
        """Return the process exit status."""
        ...

    @property
    def stdout(self) -> str:
        """Return the captured standard output."""
        ...

    @property
    def stderr(self) -> str:
        """Return the captured standard error."""
        ...


class HostCommandRunner(Protocol):
    """Runs argv directly on the host, without a shell."""

    def run(
        self,
        command: list[str],
        /,
        *,
        cwd: Path | None,
        env: dict[str, str],
        dry_run: bool,
    ) -> CommandRunResult:
        """Run ``command`` in ``cwd`` with ``env`` as its environment.

        ``dry_run`` reports the result the command would yield without running
        it.
        """
        ...


class VmCommandRunner(Protocol):
    """Runs argv inside a remote VM, optionally in a chosen directory."""

    def run_vm_command(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str],
        remote_dir: str | None,
        dry_run: bool,
    ) -> CommandRunResult:
        """Run ``argv`` in the VM, in ``remote_dir`` when one is given.

        ``env`` supplies the variables for the remote command and ``dry_run``
        reports the result it would yield without running it.
        """
        ...
