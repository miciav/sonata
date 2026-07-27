from __future__ import annotations

from contextlib import contextmanager

import pytest

from sonata_engine.workflow.context import bind_workflow_context, bind_workflow_sink
from sonata_engine.workflow.events import WorkflowContext, WorkflowEvent
from sonata_engine.workflow.reporting import status, subtask, workflow_log


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


def test_subtask_emits_started_and_passed() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with subtask(task_id="build-images/cp", title="Build control plane"):
            pass

    assert [event.kind for event in sink.events] == ["task.started", "task.passed"]
    assert sink.events[0].task_id == "build-images/cp"
    assert sink.events[0].title == "Build control plane"


def test_subtask_nests_under_the_task_that_is_running() -> None:
    """The parent is read from the bound context, not passed in: a task does not
    know the id the compiler gave it."""
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with bind_workflow_context(WorkflowContext(task_id="003.build-images")):
            with subtask(task_id="build-images/cp", title="cp"):
                pass

    assert {event.parent_task_id for event in sink.events} == {"003.build-images"}


def test_subtasks_nest_to_whatever_depth_the_call_stack_produces() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with bind_workflow_context(WorkflowContext(task_id="001.outer")):
            with subtask(task_id="outer/mid", title="mid"):
                with subtask(task_id="outer/mid/inner", title="inner"):
                    pass

    parents = {event.task_id: event.parent_task_id for event in sink.events}
    assert parents["outer/mid"] == "001.outer"
    assert parents["outer/mid/inner"] == "outer/mid"


def test_a_failing_subtask_reports_and_still_propagates() -> None:
    """Reporting must not swallow the failure: the enclosing unit has to fail."""
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with pytest.raises(RuntimeError, match="image build failed"):
            with subtask(task_id="build-images/cp", title="cp"):
                raise RuntimeError("image build failed")

    assert [event.kind for event in sink.events] == ["task.started", "task.failed"]
    assert "image build failed" in sink.events[1].detail


def test_subtask_is_a_noop_without_a_sink() -> None:
    with subtask(task_id="build-images/cp", title="cp"):
        pass  # no error, no crash
