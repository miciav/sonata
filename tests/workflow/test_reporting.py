from __future__ import annotations

from contextlib import contextmanager

from sonata_engine.workflow.context import bind_workflow_sink
from sonata_engine.workflow.events import WorkflowEvent
from sonata_engine.workflow.reporting import status, workflow_log


class _FakeSink:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []
        self.status_labels: list[str] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)

    @contextmanager
    def status(self, label: str):
        self.status_labels.append(label)
        yield


def test_workflow_log_emits_log_line() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        workflow_log("hello")
    assert sink.events[0].kind == "log.line"
    assert sink.events[0].line == "hello"


def test_status_delegates_to_sink_status() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with status("loading"):
            pass
    assert sink.status_labels == ["loading"]


def test_status_is_noop_without_sink() -> None:
    with status("loading"):
        pass  # no error, no crash


def test_helpers_are_noop_without_sink() -> None:
    workflow_log("no sink")


def test_task_lifecycle_helpers_are_not_public() -> None:
    import sonata_engine.workflow.reporting as reporting

    assert not hasattr(reporting, "phase")
    assert not hasattr(reporting, "workflow_step")
    assert not hasattr(reporting, "task_lifecycle")
    assert not hasattr(reporting, "task_skipped")
