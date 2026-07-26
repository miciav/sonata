from __future__ import annotations

import dataclasses

import pytest

from sonata_engine.core.compiled import CompiledTask, CompiledWorkflow
from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import ReusableTask, Task


class _NoopTask(Task[None]):
    def __init__(self, title: str) -> None:
        self.title = title

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        return TaskOutcome()


class _ReusableNoop(ReusableTask):
    title = "Build"

    def __init__(self, reuse_key: str) -> None:
        self._reuse_key = reuse_key

    @property
    def reuse_key(self) -> str:
        return self._reuse_key

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        return TaskOutcome()


def test_compiled_task_defaults_to_no_required_resources() -> None:
    compiled_task = CompiledTask(task_id="001.noop", task=_NoopTask("Noop"))

    assert compiled_task.required_resources == ()


def test_compiled_task_is_frozen() -> None:
    compiled_task = CompiledTask(task_id="001.noop", task=_NoopTask("Noop"))

    with pytest.raises(dataclasses.FrozenInstanceError):
        compiled_task.task_id = "002.noop"  # type: ignore[misc]


def test_compiled_workflow_is_frozen() -> None:
    compiled_task = CompiledTask(task_id="001.noop", task=_NoopTask("Noop"))
    compiled_workflow = CompiledWorkflow(workflow_id="wf", tasks=(compiled_task,))

    with pytest.raises(dataclasses.FrozenInstanceError):
        compiled_workflow.workflow_id = "other"  # type: ignore[misc]


def test_compiled_task_holds_the_only_id() -> None:
    task_instance = _NoopTask("Noop")
    compiled_task = CompiledTask(task_id="001.noop", task=task_instance)

    assert not hasattr(compiled_task.task, "task_id")
    assert compiled_task.task_id == "001.noop"


def test_reusable_task_configuration_changes_workflow_fingerprint() -> None:
    first = CompiledWorkflow(
        workflow_id="wf",
        tasks=(CompiledTask(task_id="001.build", task=_ReusableNoop("source=old")),),
    )
    second = CompiledWorkflow(
        workflow_id="wf",
        tasks=(CompiledTask(task_id="001.build", task=_ReusableNoop("source=new")),),
    )

    assert first.fingerprint != second.fingerprint
