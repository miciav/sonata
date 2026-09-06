from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, override

from sonata_engine import Task, TaskInputs, TaskOutcome


class RemoteOperationResult(Protocol):
    @property
    def return_code(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


class RemoteCommandProvider[RequestT](Protocol):
    def exec_argv(self, request: RequestT, argv: tuple[str, ...]) -> RemoteOperationResult: ...


class RemoteProvider[RequestT](RemoteCommandProvider[RequestT], Protocol):
    def transfer_to(
        self, request: RequestT, *, source: Path, destination: str
    ) -> RemoteOperationResult: ...


@dataclass
class FileTransferTask[RequestT](Task[None]):
    source: Path
    destination: str
    provider: RemoteProvider[RequestT]
    request: RequestT
    title: str = field(default="")

    def __post_init__(self) -> None:
        if not self.title:
            self.title = f"Transfer {self.source.name}"

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        del inputs
        result = self.provider.transfer_to(
            self.request, source=self.source, destination=self.destination
        )
        if not isinstance(result.return_code, int) or isinstance(result.return_code, bool):
            raise RuntimeError(f"{self.title}: transfer returned no integer return_code")
        if result.return_code != 0:
            detail = result.stderr or result.stdout
            raise RuntimeError(
                f"{self.title} failed (exit {result.return_code})"
                + (f": {detail}" if detail else "")
            )
        return TaskOutcome(value=None)
