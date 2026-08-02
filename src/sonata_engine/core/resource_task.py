from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar, override

from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import Task

T = TypeVar("T")


@dataclass(frozen=True, slots=True, eq=False)
class Resource(Generic[T]):
    """A pair of acquire/release side effects a task depends on.

    A `Resource` is not itself added to a workflow; consumer tasks reference it
    via `Workflow.add(task, requires=(resource,))`. `compile()` splices an
    acquire unit before the first consumer and a release unit after the last.

    Identity semantics (`eq=False`): the compiler keys resources by `id()` and
    deliberately treats two structurally identical `Resource` instances as
    distinct (each gets its own acquire/release pair and runtime value). Value
    equality would contradict that -- and would also make `hash()` recurse
    forever on a cyclic `requires` graph.
    """

    title: str
    acquire: Callable[[TaskInputs], T] = field(repr=False)
    release: Callable[[TaskInputs, T], None] = field(repr=False)
    requires: tuple[Resource[Any], ...] = ()
    # `keep` retains a resource so a later run need not rebuild it. A resource
    # that holds a secret -- a staged token, a signing key, an open credential
    # lease -- declares always_release instead, so leaving it behind is not
    # something a caller can cause by forgetting to classify it.
    always_release: bool = False
    # Rebuilds an acquired value from its journal record, for a teardown running
    # in a later process. The journal can only hold JSON, so a release written
    # against a dataclass needs its own type back rather than a dict.
    revive: Callable[[Any], T] | None = field(default=None, repr=False)
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
