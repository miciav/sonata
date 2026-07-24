from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import override

import pytest

from sonata_engine.core.outcome import Evidence, TaskOutcome
from sonata_engine.core.task import ReusableTask, Task


class ValidTask(Task[int]):
    title = "Valid"

    @override
    def run(self) -> TaskOutcome[int]:
        return TaskOutcome(value=42)


def test_valid_task_subclass_is_accepted() -> None:
    task = ValidTask()
    outcome = task.run()
    assert isinstance(task, Task)
    assert outcome == TaskOutcome(value=42)


def test_old_structural_dataclass_is_not_a_task() -> None:
    @dataclass
    class LegacyTask:
        task_id: str = "legacy"
        title: str = "Legacy"

        def run(self) -> int:
            return 1

    assert not isinstance(LegacyTask(), Task)


def test_task_is_abstract_and_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Task()  # type: ignore[abstract]


def test_reusable_task_returns_none_outcome() -> None:
    class MyReusable(ReusableTask):
        title = "Reusable"

        @override
        def run(self) -> TaskOutcome[None]:
            return TaskOutcome()

    task = MyReusable()
    assert isinstance(task, Task)
    assert task.reusable is True
    assert task.run() == TaskOutcome()


def test_evidence_digest_defaults_to_none() -> None:
    evidence = Evidence(kind="log", reference="s3://bucket/key")
    assert evidence.digest is None


def test_invalid_run_override_fails_static_type_check() -> None:
    """The negative fixture (InvalidTask.run returning None) must fail
    basedpyright. Run as a subprocess so the normal type-check suite
    (`uv run basedpyright`, scoped to src/) stays green.
    """
    fixture = Path(__file__).parent.parent / "typecheck" / "task_contracts.py"
    result = subprocess.run(
        [sys.executable, "-m", "basedpyright", str(fixture)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, result.stdout
    assert "reportIncompatibleMethodOverride" in result.stdout
