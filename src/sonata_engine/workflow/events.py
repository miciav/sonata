"""The event vocabulary shared by the engine and every reporting sink.

Nothing here renders: a sink receives `WorkflowEvent` objects and decides what
to do with them, so the engine can stream progress to a console, a TUI or a
test recorder without knowing which.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, frozen=True)
class WorkflowContext:
    """Identifies the run, task and step that an event belongs to.

    The engine hands a narrowed context down as it descends into tasks, so
    reporting helpers can fill in identity without every call site threading
    the ids through. The defaults describe an interactive console run outside
    any compiled workflow.
    """

    flow_id: str = "interactive.console"
    flow_run_id: str | None = None
    task_id: str | None = None
    parent_task_id: str | None = None
    task_run_id: str | None = None


@dataclass(slots=True, frozen=True)
class WorkflowEvent:
    """One reporting signal emitted by the engine.

    `kind` names the signal (`task.started`, `task.passed`, `task.skipped`,
    `log.line`, ...) and selects which payload fields the consumer reads: the
    task kinds use `title` and `detail`, while `log.line` carries `stream` and
    `line`. `at` is stamped at construction in UTC.
    """

    kind: str
    flow_id: str
    at: datetime = field(default_factory=_utc_now)
    flow_run_id: str | None = None
    task_id: str | None = None
    parent_task_id: str | None = None
    task_run_id: str | None = None
    title: str = ""
    detail: str = ""
    stream: str = "stdout"
    line: str = ""


class WorkflowSink(Protocol):
    """A destination for the engine's reporting events.

    Implement it to render workflow progress — print to a console, update TUI
    widgets, or record events for a test.
    """

    def emit(self, event: WorkflowEvent) -> None:
        """Deliver one reporting event to this sink."""
        ...

    def status(self, label: str) -> AbstractContextManager[None]:
        """Return a context manager showing `label` while its block runs."""
        ...
