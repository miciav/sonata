"""Tests for FileTransferTask."""

from dataclasses import dataclass
from pathlib import Path
from typing import override

from sonata_engine import TaskInputs

from sonata_tasks.transfer import FileTransferTask


@dataclass(frozen=True)
class _Result:
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""


class _FakeTransferProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def exec_argv(self, request: object, argv: tuple[str, ...]) -> _Result:
        raise AssertionError(f"unexpected command: {argv}")

    def transfer_to(
        self, request: object, *, source: Path, destination: str
    ) -> _Result:
        self.calls.append((source, destination))
        return _Result()


def test_file_transfer_invokes_provider_and_returns_none():
    provider = _FakeTransferProvider()
    request = object()
    task = FileTransferTask(
        source=Path("/tmp/bake.json"),
        destination="/home/user/bake.json",
        provider=provider,
        request=request,
    )
    outcome = task.run(TaskInputs.empty())
    assert outcome.value is None
    assert provider.calls == [(Path("/tmp/bake.json"), "/home/user/bake.json")]


def test_file_transfer_title_defaults_to_source_filename():
    task = FileTransferTask(
        source=Path("/tmp/buildkitd.toml"),
        destination="/remote/buildkitd.toml",
        provider=_FakeTransferProvider(),
        request=object(),
    )
    assert task.title == "Transfer buildkitd.toml"


def test_file_transfer_raises_on_nonzero_exit():
    import pytest

    class _FailingProvider(_FakeTransferProvider):
        @override
        def transfer_to(
            self, request: object, *, source: Path, destination: str
        ) -> _Result:
            return _Result(return_code=1, stderr="disk full")

    task = FileTransferTask(
        source=Path("/tmp/bake.json"),
        destination="/remote/bake.json",
        provider=_FailingProvider(),
        request=object(),
    )
    with pytest.raises(RuntimeError, match=r"Transfer bake.json failed"):
        task.run(TaskInputs.empty())
