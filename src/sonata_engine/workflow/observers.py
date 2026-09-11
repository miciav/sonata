"""Completion hooks for a workflow run.

A caller that wants to know how a run ended passes one or more observers to
``Workflow.run()``. Each is called once, after the run has finished, with a
:class:`WorkflowCompletion` describing the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WorkflowCompletion:
    """How one workflow run ended, handed to every observer.

    ``error`` is the exception that ended the run, or None when it succeeded.
    It is the same object the caller sees, so an observer can inspect or log it
    without changing the failure the run raises.
    """

    workflow_id: str
    started_at: datetime
    finished_at: datetime
    error: BaseException | None


class WorkflowObserver(Protocol):
    """A hook notified once, when a workflow run finishes.

    Implementations are called after the run has completed its own cleanup, so
    they observe the result rather than participate in it. An observer that
    raises does not change the run's outcome: the failure is attached to the
    run's own error as a note, and discarded when the run succeeded.
    """

    def finished(self, completion: WorkflowCompletion) -> None:
        """Handle the end of the run described by ``completion``."""
        ...
