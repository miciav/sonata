from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import override

from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import Task


@dataclass(frozen=True, slots=True)
class Resource:
    """A pair of acquire/release side effects a task depends on.

    A `Resource` is not itself added to a workflow; consumer tasks reference it
    via `Workflow.add(task, requires=(resource,))`. `compile()` splices an
    acquire unit before the first consumer and a release unit after the last.
    """

    title: str
    acquire: Callable[[], None] = field(repr=False)
    release: Callable[[], None] = field(repr=False)
    infrastructure: bool = False

    @property
    def release_title(self) -> str:
        return f"Release {self.title.removeprefix('Acquire ')}"


@dataclass(frozen=True, slots=True, eq=True)
class ResourceOp(Task[None]):
    """Engine-owned `Task[None]` wrapping a single acquire or release callable.

    The compiler builds these when it inserts a resource's lifecycle into the
    compiled sequence, so acquire/release are ordinary compiled task units.
    Value equality (a stable callable identity) keeps `compile()` deterministic.
    """

    title: str
    fn: Callable[[], None] = field(repr=False)

    @override
    def run(self) -> TaskOutcome[None]:
        self.fn()
        return TaskOutcome(value=None)
