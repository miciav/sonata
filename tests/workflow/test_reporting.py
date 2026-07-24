from __future__ import annotations

from contextlib import contextmanager

import pytest

from sonata_engine.workflow.context import bind_workflow_context, bind_workflow_sink
from sonata_engine.workflow.events import WorkflowContext, WorkflowEvent
from sonata_engine.workflow.reporting import (
    phase,
    status,
    task_lifecycle,
    workflow_log,
    workflow_step,
)


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


def test_phase_emits_phase_started() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        phase("Provisioning")
    assert sink.events[0].kind == "phase.started"
    assert sink.events[0].title == "Provisioning"


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
    phase("no sink")
    workflow_log("no sink")


def test_workflow_step_emits_running_then_completed() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with workflow_step(task_id="vm.up", title="Start VM"):
            pass
    assert [e.kind for e in sink.events] == ["task.running", "task.completed"]
    assert all(e.task_id == "vm.up" for e in sink.events)


def test_workflow_step_emits_failed_on_exception() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with pytest.raises(RuntimeError, match="boom"):
            with workflow_step(task_id="vm.up", title="Start VM"):
                raise RuntimeError("boom")
    assert [e.kind for e in sink.events] == ["task.running", "task.failed"]


def test_workflow_step_propagates_parent_task_id_from_context() -> None:
    sink = _FakeSink()
    ctx = WorkflowContext(flow_id="e2e.k3s", task_id="tests.run_checks")
    with bind_workflow_sink(sink), bind_workflow_context(ctx):
        with workflow_step(task_id="verify.health", title="Verifying health"):
            pass
    assert all(e.parent_task_id == "tests.run_checks" for e in sink.events)


def test_task_lifecycle_emits_started_then_passed() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with task_lifecycle(task_id="001.build", title="Build"):
            pass
    assert [e.kind for e in sink.events] == ["task.started", "task.passed"]
    assert all(e.task_id == "001.build" for e in sink.events)


def test_task_lifecycle_emits_failed_on_exception() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with pytest.raises(RuntimeError, match="boom"):
            with task_lifecycle(task_id="001.build", title="Build"):
                raise RuntimeError("boom")
    assert [e.kind for e in sink.events] == ["task.started", "task.failed"]


def test_task_lifecycle_is_noop_without_sink() -> None:
    with task_lifecycle(task_id="001.build", title="Build"):
        pass  # no error, no crash
