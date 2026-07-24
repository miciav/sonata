from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from sonata_engine.core.resource_task import Resource
from sonata_engine.core.task import Task

T = TypeVar("T")

TaskKind = Literal["consumer", "acquire", "release"]


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
