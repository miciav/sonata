from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, override

from sonata_engine.core.inputs import TaskInputs
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
    def run(self, inputs: TaskInputs) -> TaskOutcome[T]:
        raise NotImplementedError

    def _fingerprint_payload(self) -> object:
        """What this task contributes to the workflow's resume fingerprint.

        `None` for a task whose identity is fully described by its class and
        position. A task that owns children returns something covering them, so
        editing them invalidates resume. Must be JSON-serializable.
        """
        return None


class ReusableTask(Task[None], ABC):
    """A task eligible for skip-on-verified-evidence.

    `reuse_key` identifies the task's semantic configuration independently from its
    compiler-owned task ID. It must change whenever inputs that affect reusable output
    change.
    """

    reusable: bool = True

    @property
    @abstractmethod
    def reuse_key(self) -> str:
        raise NotImplementedError

    @override
    @abstractmethod
    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        raise NotImplementedError

    @override
    def _fingerprint_payload(self) -> object:
        return self.reuse_key
