"""The base classes every unit of work in a workflow derives from."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import override

from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome


class Task[T](ABC):
    """Base class for composable workflow tasks.

    Subclasses must implement `run` and return a `TaskOutcome[T]`; the
    outcome's `value` is an in-process data channel and is never
    serialized by the journal.
    """

    title: str
    idempotent: bool = False

    @abstractmethod
    def run(self, inputs: TaskInputs) -> TaskOutcome[T]:
        """Carry out this task and return its outcome.

        The runner calls this once per attempt with the inputs it built for the
        task. A subclass must return a `TaskOutcome`, not a bare value.
        """
        raise NotImplementedError

    def _fingerprint_payload(self) -> object:
        """Return this task's contribution to the workflow's resume fingerprint.

        `None` for a task whose identity is fully described by its class and
        position. A task that owns children returns something covering them, so
        editing them invalidates resume. Must be JSON-serializable.
        """
        return None


class ReusableTask(Task[None], ABC):
    """A task eligible for skip-on-verified-evidence.

    `reuse_key` identifies the task's semantic configuration independently from its
    compiler-owned task ID. It must change whenever inputs that affect reusable output
    change. A subclass may set `reusable = False` to opt out of skipping while
    retaining the evidence-only outcome contract.
    """

    reusable: bool = True

    @property
    @abstractmethod
    def reuse_key(self) -> str:
        """Return a key that changes whenever reusable output would change.

        Resume compares it against the journal, so it must cover every input
        that affects what the task produces.
        """
        raise NotImplementedError

    @override
    @abstractmethod
    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        raise NotImplementedError

    @override
    def _fingerprint_payload(self) -> object:
        return self.reuse_key
