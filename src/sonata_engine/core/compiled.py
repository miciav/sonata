from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.resource_task import Resource
from sonata_engine.core.task import Task

T = TypeVar("T")

TaskKind = Literal["consumer", "acquire", "release"]
TaskExecutionStatus = Literal["passed", "skipped"]


@dataclass(frozen=True, slots=True)
class CompiledTask(Generic[T]):
    """A task with its compiler-assigned, stable identity.

    `task_id` only ever exists here; `Task` instances never carry one.

    `kind` discriminates ordinary consumer units from the acquire/release units
    the compiler splices in for resources. For an `acquire`/`release` unit,
    `resource` names which `Resource` it belongs to, letting `run_compiled`
    pair a release with its acquire and release out of linear order on failure.
    """

    task_id: str
    task: Task[T]
    required_resources: tuple[Resource, ...] = ()
    kind: TaskKind = "consumer"
    resource: Resource | None = None


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """The immutable result of `Workflow.compile()`."""

    workflow_id: str
    tasks: tuple[CompiledTask[object], ...]

    @property
    def fingerprint(self) -> str:
        """Deterministic identity of the ordered compiled topology."""
        topology = [
            (
                task.task_id,
                task.kind,
                f"{type(task.task).__module__}.{type(task.task).__qualname__}",
            )
            for task in self.tasks
        ]
        canonical = json.dumps(
            [self.workflow_id, topology],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True, slots=True)
class TaskExecution:
    """The runtime result of one compiled task unit."""

    task_id: str
    status: TaskExecutionStatus
    outcome: TaskOutcome[Any] | None


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """Immutable results from one successful workflow run."""

    workflow_id: str
    tasks: tuple[TaskExecution, ...]

    def by_id(self, task_id: str) -> TaskExecution:
        for execution in self.tasks:
            if execution.task_id == task_id:
                return execution
        raise KeyError(task_id)
