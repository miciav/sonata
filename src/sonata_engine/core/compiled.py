from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from sonata_engine.core.task import Task

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CompiledTask(Generic[T]):
    """A task with its compiler-assigned, stable identity.

    `task_id` only ever exists here; `Task` instances never carry one.
    """

    task_id: str
    task: Task[T]
    required_resources: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """The immutable result of `Workflow.compile()`."""

    workflow_id: str
    tasks: tuple[CompiledTask[object], ...]
