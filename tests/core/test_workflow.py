from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

import pytest

from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import Task
from sonata_engine.core.workflow import Workflow
from sonata_engine.errors import InvalidTaskOutcomeError
from sonata_engine.workflow.context import bind_workflow_sink
from sonata_engine.workflow.events import WorkflowEvent


class _NoopTask(Task[None]):
    """A minimal real `Task[T]` subclass, used to exercise the compiler."""

    def __init__(self, title: str) -> None:
        self.title = title

    def run(self) -> TaskOutcome[None]:
        return TaskOutcome()


@dataclass
class _OkTask:
    task_id: str
    title: str
    calls: list[str]

    def run(self) -> None:
        self.calls.append(self.task_id)


@dataclass
class _FailTask:
    task_id: str
    title: str
    calls: list[str]

    def run(self) -> None:
        self.calls.append(self.task_id)
        raise RuntimeError(f"{self.task_id} failed")


def test_workflow_runs_tasks_in_order() -> None:
    calls: list[str] = []
    workflow = Workflow(
        tasks=[
            _OkTask(task_id="a", title="A", calls=calls),
            _OkTask(task_id="b", title="B", calls=calls),
            _OkTask(task_id="c", title="C", calls=calls),
        ]
    )
    workflow.run()
    assert calls == ["a", "b", "c"]


def test_workflow_stops_on_first_failure() -> None:
    calls: list[str] = []
    workflow = Workflow(
        tasks=[
            _OkTask(task_id="a", title="A", calls=calls),
            _FailTask(task_id="b", title="B", calls=calls),
            _OkTask(task_id="c", title="C", calls=calls),
        ]
    )
    with pytest.raises(RuntimeError, match="b failed"):
        workflow.run()
    assert calls == ["a", "b"]
    assert "c" not in calls


def test_workflow_cleanup_tasks_always_run() -> None:
    calls: list[str] = []
    workflow = Workflow(
        tasks=[
            _OkTask(task_id="a", title="A", calls=calls),
            _FailTask(task_id="b", title="B", calls=calls),
        ],
        cleanup_tasks=[
            _OkTask(task_id="cleanup", title="Cleanup", calls=calls),
        ],
    )
    with pytest.raises(RuntimeError, match="b failed"):
        workflow.run()
    assert "cleanup" in calls


def test_workflow_cleanup_runs_after_success_too() -> None:
    calls: list[str] = []
    workflow = Workflow(
        tasks=[_OkTask(task_id="a", title="A", calls=calls)],
        cleanup_tasks=[_OkTask(task_id="cleanup", title="Cleanup", calls=calls)],
    )
    workflow.run()
    assert calls == ["a", "cleanup"]


def test_keep_infrastructure_does_not_skip_static_cleanup_tasks() -> None:
    """`keep_infrastructure` only retains `infrastructure`-flagged `Resource`s in the
    compiled `run_compiled()` path; the legacy `cleanup_tasks` list is unconditional."""
    calls: list[str] = []
    workflow = Workflow(
        tasks=[_OkTask(task_id="a", title="A", calls=calls)],
        cleanup_tasks=[_OkTask(task_id="cleanup", title="Cleanup", calls=calls)],
        keep_infrastructure=True,
    )

    workflow.run()

    assert calls == ["a", "cleanup"]


def test_workflow_task_ids_includes_all_tasks() -> None:
    calls: list[str] = []
    workflow = Workflow(
        tasks=[
            _OkTask(task_id="a", title="A", calls=calls),
            _OkTask(task_id="b", title="B", calls=calls),
        ],
        cleanup_tasks=[
            _OkTask(task_id="cleanup", title="Cleanup", calls=calls),
        ],
    )
    assert workflow.task_ids == ["a", "b", "cleanup"]


def test_workflow_cleanup_error_raised_after_main_error() -> None:
    calls: list[str] = []
    workflow = Workflow(
        tasks=[_FailTask(task_id="main", title="Main", calls=calls)],
        cleanup_tasks=[_FailTask(task_id="cleanup", title="Cleanup", calls=calls)],
    )
    with pytest.raises(RuntimeError) as exc_info:
        workflow.run()
    assert "main failed" in str(exc_info.value)
    assert "cleanup failed" in str(exc_info.value)


def test_workflow_with_no_tasks_runs_cleanly() -> None:
    workflow = Workflow(tasks=[])
    workflow.run()  # should not raise


def test_workflow_cleanup_only_failure_raised() -> None:
    calls: list[str] = []
    workflow = Workflow(
        tasks=[_OkTask(task_id="a", title="A", calls=calls)],
        cleanup_tasks=[_FailTask(task_id="cleanup", title="Cleanup", calls=calls)],
    )
    with pytest.raises(RuntimeError, match="Cleanup failed"):
        workflow.run()
    assert calls == ["a", "cleanup"]


# --- Compiler: add()/compile() -------------------------------------------


def test_compile_preserves_insertion_order() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_NoopTask("Prepare source"))
    workflow.add(_NoopTask("Build amd64"))
    workflow.add(_NoopTask("Publish manifest"))

    compiled = workflow.compile()

    assert [ct.task.title for ct in compiled.tasks] == [
        "Prepare source",
        "Build amd64",
        "Publish manifest",
    ]


def test_compile_generates_ordinal_slug_ids() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_NoopTask("Prepare source"))
    workflow.add(_NoopTask("Build amd64"))
    workflow.add(_NoopTask("Publish manifest"))

    compiled = workflow.compile()

    assert [ct.task_id for ct in compiled.tasks] == [
        "001.prepare-source",
        "002.build-amd64",
        "003.publish-manifest",
    ]


def test_compile_disambiguates_duplicate_titles_by_ordinal() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_NoopTask("Build"))
    workflow.add(_NoopTask("Build"))

    compiled = workflow.compile()

    ids = [ct.task_id for ct in compiled.tasks]
    assert ids == ["001.build", "002.build"]
    assert len(set(ids)) == 2


def test_task_objects_expose_no_task_id() -> None:
    task_instance = _NoopTask("Prepare source")
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(task_instance)

    compiled = workflow.compile()

    assert not hasattr(task_instance, "task_id")
    assert compiled.tasks[0].task_id == "001.prepare-source"


def test_repeated_compilation_is_deterministic() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_NoopTask("Prepare source"))
    workflow.add(_NoopTask("Build amd64"))

    first = workflow.compile()
    second = workflow.compile()

    assert first == second
    assert first.workflow_id == second.workflow_id == "wf"


def test_compile_requires_non_empty_workflow_id() -> None:
    workflow = Workflow(tasks=[])
    workflow.add(_NoopTask("Prepare source"))

    with pytest.raises(ValueError, match="workflow_id"):
        workflow.compile()


# --- run_compiled(): lifecycle events -------------------------------------


class _FakeSink:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)

    @contextmanager
    def status(self, label: str) -> Generator[None, None, None]:
        yield


class _BoomTask(Task[None]):
    title = "Boom"

    def run(self) -> TaskOutcome[None]:
        raise RuntimeError("boom")


class _BadOutcomeTask(Task[None]):
    title = "Bad outcome"

    def run(self) -> TaskOutcome[None]:
        return "not-an-outcome"  # type: ignore[return-value]


def test_run_compiled_emits_started_then_passed() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_NoopTask("Prepare source"))
    compiled = workflow.compile()

    sink = _FakeSink()
    with bind_workflow_sink(sink):
        workflow.run_compiled(compiled)

    assert [e.kind for e in sink.events] == ["task.started", "task.passed"]
    assert all(e.task_id == "001.prepare-source" for e in sink.events)


def test_run_compiled_emits_failed_on_exception_and_propagates() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_BoomTask())
    compiled = workflow.compile()

    sink = _FakeSink()
    with bind_workflow_sink(sink), pytest.raises(RuntimeError, match="boom"):
        workflow.run_compiled(compiled)

    assert [e.kind for e in sink.events] == ["task.started", "task.failed"]
    assert all(e.task_id == "001.boom" for e in sink.events)


def test_run_compiled_rejects_non_task_outcome_result() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_BadOutcomeTask())
    compiled = workflow.compile()

    sink = _FakeSink()
    with bind_workflow_sink(sink), pytest.raises(InvalidTaskOutcomeError):
        workflow.run_compiled(compiled)

    assert [e.kind for e in sink.events] == ["task.started", "task.failed"]


def test_run_compiled_stops_on_first_failure() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_BoomTask())
    workflow.add(_NoopTask("Never runs"))
    compiled = workflow.compile()

    sink = _FakeSink()
    with bind_workflow_sink(sink), pytest.raises(RuntimeError, match="boom"):
        workflow.run_compiled(compiled)

    assert [e.kind for e in sink.events] == ["task.started", "task.failed"]


def test_run_compiled_is_noop_safe_without_sink() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_NoopTask("Prepare source"))
    compiled = workflow.compile()

    workflow.run_compiled(compiled)  # no sink bound, must not raise
