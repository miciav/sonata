from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar, override

from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import Task

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Resource(Generic[T]):
    """A pair of acquire/release side effects a task depends on.

    A `Resource` is not itself added to a workflow; consumer tasks reference it
    via `Workflow.add(task, requires=(resource,))`. `compile()` splices an
    acquire unit before the first consumer and a release unit after the last.
    """

    title: str
    acquire: Callable[[TaskInputs], T] = field(repr=False)
    release: Callable[[TaskInputs, T], None] = field(repr=False)
    requires: tuple[Resource[Any], ...] = ()
    infrastructure: bool = False
    acquire_idempotent: bool = False

    @property
    def release_title(self) -> str:
        return f"Release {self.title.removeprefix('Acquire ')}"


class ResourceOperation(StrEnum):
    ACQUIRE = "acquire"
    RELEASE = "release"


@dataclass(frozen=True, slots=True, eq=True)
class ResourceOp(Task[object]):
    """Engine-owned lifecycle unit identified by a resource and operation."""

    title: str
    resource: Resource[Any] = field(repr=False)
    operation: ResourceOperation = ResourceOperation.ACQUIRE
    idempotent: bool = False

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[object]:
        if self.operation is ResourceOperation.ACQUIRE:
            return TaskOutcome(value=self.resource.acquire(inputs))
        self.resource.release(inputs, inputs.resource(self.resource))
        return TaskOutcome(value=None)
