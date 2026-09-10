"""Ports for remote execution and the task that copies a file to a host."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, override

from sonata_engine import Task, TaskInputs, TaskOutcome


class RemoteOperationResult(Protocol):
    """The outcome of one remote operation."""

    @property
    def return_code(self) -> int:
        """The command's exit status."""
        ...

    @property
    def stdout(self) -> str:
        """Whatever the command wrote to standard output."""
        ...

    @property
    def stderr(self) -> str:
        """Whatever the command wrote to standard error."""
        ...


class RemoteCommandProvider[RequestT](Protocol):
    """A provider that can run a command on a remote target."""

    def exec_argv(
        self, request: RequestT, argv: tuple[str, ...]
    ) -> RemoteOperationResult:
        """Run ``argv`` on the target described by ``request``."""
        ...


class RemoteProvider[RequestT](RemoteCommandProvider[RequestT], Protocol):
    """A command provider that can also copy files to the remote target."""

    def transfer_to(
        self, request: RequestT, *, source: Path, destination: str
    ) -> RemoteOperationResult:
        """Copy the local ``source`` file to ``destination`` on the target."""
        ...


@dataclass
class FileTransferTask[RequestT](Task[None]):
    """Copy a local file to a remote destination and check that it arrived."""

    source: Path
    destination: str
    provider: RemoteProvider[RequestT]
    request: RequestT
    title: str = field(default="")

    def __post_init__(self) -> None:
        """Default ``title`` to one naming the source file."""
        if not self.title:
            self.title = f"Transfer {self.source.name}"

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        """Transfer the file, raising if the transfer reports a failure.

        The task inputs are unused; the destination comes from the fields.
        """
        del inputs
        result = self.provider.transfer_to(
            self.request, source=self.source, destination=self.destination
        )
        if not isinstance(result.return_code, int) or isinstance(
            result.return_code, bool
        ):
            raise RuntimeError(
                f"{self.title}: transfer returned no integer return_code"
            )
        if result.return_code != 0:
            detail = result.stderr or result.stdout
            raise RuntimeError(
                f"{self.title} failed (exit {result.return_code})"
                + (f": {detail}" if detail else "")
            )
        return TaskOutcome(value=None)
