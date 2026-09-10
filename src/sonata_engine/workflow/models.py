"""Durable records for workflow and task runs, as written to the run journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


WorkflowState = Literal["pending", "running", "success", "failed", "cancelled"]
"""The states a workflow run or task run moves through."""


@dataclass(slots=True, frozen=True)
class WorkflowRun:
    """One run of a compiled workflow, as persisted in the run journal.

    Records which flow was run, which orchestrator backend drove it, and when
    it started and finished (`None` until the corresponding edge is reached).
    """

    flow_id: str
    flow_run_id: str
    status: str = "pending"
    orchestrator_backend: str = "none"
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class TaskDefinition:
    """A task's identity and display text, independent of any run."""

    task_id: str
    title: str = ""
    detail: str = ""


@dataclass(slots=True, frozen=True)
class TaskRun:
    """One task's execution within a workflow run.

    `task_run_id` is unique per execution, so a task retried or resumed under
    the same `flow_id` is a distinct record.
    """

    flow_id: str
    task_id: str
    task_run_id: str
    status: str = "pending"
    title: str = ""
    detail: str = ""
