from __future__ import annotations

import pytest

from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.resource_task import Resource
from sonata_engine.core.task import Task
from sonata_engine.core.workflow import Workflow


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
