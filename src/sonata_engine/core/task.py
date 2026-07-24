from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, override

from sonata_engine.core.outcome import TaskOutcome

T = TypeVar("T")


class Task(Generic[T], ABC):
    """Base class for composable workflow tasks.

    Subclasses must implement `run` and return a `TaskOutcome[T]`; the
    outcome's `value` is an in-process data channel and is never
    serialized by the journal.
    """

    title: str
    idempotent: bool = False

    @abstractmethod
    def run(self) -> TaskOutcome[T]:
        raise NotImplementedError


class ReusableTask(Task[None], ABC):
    """A task with no mutable result channel, eligible for skip-on-verified-evidence."""

    reusable: bool = True

    @override
    @abstractmethod
    def run(self) -> TaskOutcome[None]:
        raise NotImplementedError
