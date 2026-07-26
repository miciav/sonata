"""Static-check fixture for the Task/TaskOutcome contract.

Not collected by pytest (module name does not match test discovery
patterns). Type-checked directly via subprocess in
tests/core/test_task.py::test_invalid_run_override_fails_static_type_check.
"""

from __future__ import annotations

from typing import override

from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import ReusableTask, Task


class ValidTask(Task[int]):
    title = "Valid"

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[int]:
        return TaskOutcome(value=42)


class InvalidTask(Task[int]):
    """Invalid override: return type does not satisfy Task[int].run."""

    title = "Invalid"

    @override
    def run(self, inputs: TaskInputs) -> None:
        return None


class MissingReuseKey(ReusableTask):
    title = "Missing reuse key"

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        return TaskOutcome()


MissingReuseKey()
