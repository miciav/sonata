from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pytest

from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.resource_task import Resource
from sonata_engine.core.task import Task
from sonata_engine.core.workflow import Workflow
from sonata_engine.journal import Journal, JournalConfig
from sonata_engine.workflow.context import bind_workflow_sink
from sonata_engine.workflow.events import WorkflowEvent


class _RecordTask(Task[None]):
    """A real `Task[None]` consumer that records its title and can fail."""

    def __init__(self, title: str, calls: list[str], *, fail: bool = False) -> None:
        self.title = title
        self._calls = calls
        self._fail = fail

    def run(self) -> TaskOutcome[None]:
        self._calls.append(self.title)
        if self._fail:
            raise RuntimeError(f"{self.title} failed")
        return TaskOutcome()


def _resource(name: str, calls: list[str], *, infrastructure: bool = False) -> Resource:
    return Resource(
        title=f"Acquire {name}",
        acquire=lambda: calls.append(f"acquire.{name}"),
        release=lambda: calls.append(f"release.{name}"),
        infrastructure=infrastructure,
    )


def _workflow(**kwargs: object) -> Workflow:
    return Workflow(workflow_id="wf", **kwargs)  # type: ignore[arg-type]


class _FailAcquirePassedSink:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)
        if event.kind == "task.passed" and event.task_id == "001.acquire-db":
            raise OSError("event sink failed")

    @contextmanager
    def status(self, _label: str) -> Generator[None, None, None]:
        yield


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)

    @contextmanager
    def status(self, _label: str) -> Generator[None, None, None]:
        yield


# --- Step 1: topology --------------------------------------------------------


def test_acquire_before_first_consumer_release_after_last() -> None:
    calls: list[str] = []
    db = _resource("db", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("Prepare", calls))
    workflow.add(_RecordTask("Use1", calls), requires=(db,))
    workflow.add(_RecordTask("Use2", calls), requires=(db,))
    workflow.add(_RecordTask("Finish", calls))

    compiled = workflow.compile()

    assert [ct.task.title for ct in compiled.tasks] == [
        "Prepare",
        "Acquire db",
        "Use1",
        "Use2",
        "Release db",
        "Finish",
    ]
    assert [ct.kind for ct in compiled.tasks] == [
        "consumer",
        "acquire",
        "consumer",
        "consumer",
        "release",
        "consumer",
    ]


def test_resource_entries_get_ordinal_slug_ids() -> None:
    calls: list[str] = []
    db = _resource("db", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("Prepare", calls))
    workflow.add(_RecordTask("Use1", calls), requires=(db,))
    workflow.add(_RecordTask("Use2", calls), requires=(db,))
    workflow.add(_RecordTask("Finish", calls))

    compiled = workflow.compile()

    assert [ct.task_id for ct in compiled.tasks] == [
        "001.prepare",
        "002.acquire-db",
        "003.use1",
        "004.use2",
        "005.release-db",
        "006.finish",
    ]


def test_repeated_resource_compilation_is_deterministic() -> None:
    calls: list[str] = []
    db = _resource("db", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("Use", calls), requires=(db,))

    assert workflow.compile() == workflow.compile()


def test_overlapping_resources_release_in_reverse_at_shared_point() -> None:
    calls: list[str] = []
    a = _resource("a", calls)
    b = _resource("b", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("T1", calls), requires=(a,))
    workflow.add(_RecordTask("T2", calls), requires=(a, b))

    compiled = workflow.compile()

    assert [ct.task.title for ct in compiled.tasks] == [
        "Acquire a",
        "T1",
        "Acquire b",
        "T2",
        "Release b",
        "Release a",
    ]


def test_single_consumer_resource_wraps_that_consumer() -> None:
    calls: list[str] = []
    r = _resource("r", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("Only", calls), requires=(r,))

    compiled = workflow.compile()

    assert [ct.task.title for ct in compiled.tasks] == ["Acquire r", "Only", "Release r"]


# --- Step 2: lifecycle -------------------------------------------------------


def test_releases_run_in_reverse_acquisition_order() -> None:
    calls: list[str] = []
    first = _resource("first", calls)
    second = _resource("second", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("Use", calls), requires=(first, second))

    workflow.run()

    assert calls == [
        "acquire.first",
        "acquire.second",
        "Use",
        "release.second",
        "release.first",
    ]


def test_consumer_failure_releases_acquired_in_reverse() -> None:
    calls: list[str] = []
    first = _resource("first", calls)
    second = _resource("second", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("A", calls), requires=(first,))
    workflow.add(_RecordTask("B", calls), requires=(second,))
    workflow.add(_RecordTask("Boom", calls, fail=True), requires=(first, second))

    with pytest.raises(RuntimeError, match="Boom failed"):
        workflow.run()

    assert calls == [
        "acquire.first",
        "A",
        "acquire.second",
        "B",
        "Boom",
        "release.second",
        "release.first",
    ]


def test_acquire_failure_releases_earlier_resources_only() -> None:
    calls: list[str] = []
    good = _resource("good", calls)

    def bad_acquire() -> None:
        raise RuntimeError("acquire exploded")

    bad = Resource(
        title="Acquire bad",
        acquire=bad_acquire,
        release=lambda: calls.append("release.bad"),
    )
    workflow = _workflow()
    workflow.add(_RecordTask("A", calls), requires=(good,))
    workflow.add(_RecordTask("B", calls), requires=(bad,))

    with pytest.raises(RuntimeError, match="acquire exploded"):
        workflow.run()

    assert calls == ["acquire.good", "A", "release.good"]
    assert "release.bad" not in calls


def test_consumer_failure_and_release_failure_are_combined() -> None:
    def fail_release() -> None:
        raise RuntimeError("release failed")

    resource = Resource(title="Acquire res", acquire=lambda: None, release=fail_release)
    workflow = _workflow()
    workflow.add(_RecordTask("Boom", [], fail=True), requires=(resource,))

    with pytest.raises(RuntimeError) as exc_info:
        workflow.run()

    assert "Boom failed" in str(exc_info.value)
    assert "release failed" in str(exc_info.value)


def test_release_failure_alone_is_raised() -> None:
    def fail_release() -> None:
        raise RuntimeError("release failed")

    resource = Resource(title="Acquire res", acquire=lambda: None, release=fail_release)
    workflow = _workflow()
    workflow.add(_RecordTask("Use", []), requires=(resource,))

    with pytest.raises(RuntimeError, match="Cleanup failed"):
        workflow.run()


def test_keep_infrastructure_retains_infra_but_releases_safety() -> None:
    calls: list[str] = []
    vm = _resource("vm", calls, infrastructure=True)
    port_forward = _resource("port-forward", calls)
    workflow = _workflow(keep_infrastructure=True)
    workflow.add(_RecordTask("Use", calls), requires=(vm, port_forward))

    workflow.run()

    assert calls == ["acquire.vm", "acquire.port-forward", "Use", "release.port-forward"]
    assert "release.vm" not in calls


def test_keep_infrastructure_still_releases_safety_after_failure() -> None:
    calls: list[str] = []
    vm = _resource("vm", calls, infrastructure=True)
    port_forward = _resource("port-forward", calls)
    workflow = _workflow(keep_infrastructure=True)
    workflow.add(_RecordTask("Boom", calls, fail=True), requires=(vm, port_forward))

    with pytest.raises(RuntimeError, match="Boom failed"):
        workflow.run()

    assert "release.port-forward" in calls
    assert "release.vm" not in calls


def test_acquired_resource_is_released_if_terminal_event_emission_fails() -> None:
    calls: list[str] = []
    db = _resource("db", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("Use", calls), requires=(db,))

    with bind_workflow_sink(_FailAcquirePassedSink()), pytest.raises(
        OSError, match="event sink failed"
    ):
        workflow.run()

    assert calls == ["acquire.db", "release.db"]


def test_acquired_resource_is_released_if_passed_journal_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    db = _resource("db", calls)
    workflow = _workflow()
    workflow.add(_RecordTask("Use", calls), requires=(db,))
    original = Journal.record_passed

    def _fail_acquire_passed(
        self: Journal,
        task_id: str,
        attempt: int,
        evidence: tuple = (),
    ) -> None:
        if task_id == "001.acquire-db":
            raise OSError("journal failed")
        original(self, task_id, attempt, evidence)

    monkeypatch.setattr(Journal, "record_passed", _fail_acquire_passed)

    with pytest.raises(OSError, match="journal failed"):
        workflow.run(journal=JournalConfig(tmp_path / "journal.jsonl"))

    assert calls == ["acquire.db", "release.db"]


def test_retained_infrastructure_emits_and_journals_skipped(tmp_path: Path) -> None:
    calls: list[str] = []
    vm = _resource("vm", calls, infrastructure=True)
    workflow = _workflow(keep_infrastructure=True)
    workflow.add(_RecordTask("Use", calls), requires=(vm,))
    config = JournalConfig(tmp_path / "journal.jsonl")
    sink = _RecordingSink()

    with bind_workflow_sink(sink):
        workflow.run(journal=config)

    skipped_events = [event for event in sink.events if event.kind == "task.skipped"]
    assert [event.task_id for event in skipped_events] == ["003.release-vm"]
    records = [
        json.loads(line)
        for line in config.path.read_text().splitlines()
        if line.strip()
    ]
    release_statuses = [
        record["status"]
        for record in records
        if record["task_id"] == "003.release-vm"
    ]
    assert release_statuses == ["pending", "skipped"]


def test_all_finalizers_run_after_release_and_journal_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _failed_release() -> None:
        calls.append("release.second")
        raise RuntimeError("release failed")

    first = _resource("first", calls)
    second = Resource(
        title="Acquire second",
        acquire=lambda: calls.append("acquire.second"),
        release=_failed_release,
    )
    workflow = _workflow()
    workflow.add(_RecordTask("Use", calls), requires=(first, second))
    original = Journal.record_started

    def _fail_second_release_journal(
        self: Journal, task_id: str, attempt: int
    ) -> None:
        if task_id.endswith("release-second"):
            raise OSError("journal failed")
        original(self, task_id, attempt)

    monkeypatch.setattr(Journal, "record_started", _fail_second_release_journal)

    with pytest.raises(RuntimeError, match="Cleanup failed"):
        workflow.run(journal=JournalConfig(tmp_path / "journal.jsonl"))

    assert calls[-2:] == ["release.second", "release.first"]
