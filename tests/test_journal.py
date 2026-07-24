from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from sonata_engine.core.outcome import Evidence, TaskOutcome
from sonata_engine.core.resource_task import Resource
from sonata_engine.core.task import ReusableTask, Task
from sonata_engine.core.workflow import Workflow
from sonata_engine.journal import JournalConfig


class _Ok(Task[None]):
    def __init__(self, title: str, evidence: tuple[Evidence, ...] = ()) -> None:
        self.title = title
        self._evidence = evidence

    def run(self) -> TaskOutcome[None]:
        return TaskOutcome(evidence=self._evidence)


class _ReusableOk(ReusableTask):
    def __init__(self, title: str, evidence: tuple[Evidence, ...] = ()) -> None:
        self.title = title
        self._evidence = evidence
        self.ran = False

    def run(self) -> TaskOutcome[None]:
        self.ran = True
        return TaskOutcome(evidence=self._evidence)


class _Boom(Task[None]):
    title = "Boom"

    def run(self) -> TaskOutcome[None]:
        raise RuntimeError("boom")


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _five_task_workflow() -> Workflow:
    workflow = Workflow(workflow_id="release")
    for index in range(5):
        workflow.add(_Ok(f"Task {index}"))
    return workflow


# --- Step 1: topology --------------------------------------------------------


def test_five_tasks_produce_five_logical_entries(tmp_path: Path) -> None:
    workflow = _five_task_workflow()
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    workflow.run( journal=config)

    records = _records(config.path)
    assert len({r["task_id"] for r in records}) == 5
    assert all(r["schema_version"] == 2 for r in records)
    assert len([r for r in records if r["status"] == "started"]) == 5
    assert len([r for r in records if r["status"] == "passed"]) == 5


def test_retries_add_attempts_without_new_logical_task(tmp_path: Path) -> None:
    workflow = _five_task_workflow()
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    # Ordinary (non-reusable) tasks: a passed task runs again on the next run.
    workflow.run( journal=config)
    workflow.run( journal=config)

    records = _records(config.path)
    assert len({r["task_id"] for r in records}) == 5  # still five logical tasks
    assert max(r["attempt"] for r in records) == 2
    for task_id in {r["task_id"] for r in records}:
        assert {r["attempt"] for r in records if r["task_id"] == task_id} == {1, 2}


# --- Step 2: automatic recording --------------------------------------------


def test_task_run_never_receives_a_journal_object() -> None:
    # The engine records to the journal; task bodies stay `() -> TaskOutcome`.
    assert list(inspect.signature(_Ok.run).parameters) == ["self"]
    assert list(inspect.signature(_Boom.run).parameters) == ["self"]


def test_records_started_then_passed(tmp_path: Path) -> None:
    workflow = Workflow(workflow_id="wf")
    workflow.add(_Ok("Build"))
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    workflow.run( journal=config)

    records = _records(config.path)
    assert [r["status"] for r in records] == ["started", "passed"]
    assert records[0]["started_at"] is not None
    assert records[1]["finished_at"] is not None
    # The terminal record repeats the attempt's started_at.
    assert records[1]["started_at"] == records[0]["started_at"]


def test_records_started_then_failed(tmp_path: Path) -> None:
    workflow = Workflow(workflow_id="wf")
    workflow.add(_Boom())
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    with pytest.raises(RuntimeError, match="boom"):
        workflow.run( journal=config)

    records = _records(config.path)
    assert [r["status"] for r in records] == ["started", "failed"]


def test_records_finalizer_outcomes(tmp_path: Path) -> None:
    resource = Resource(title="Acquire db", acquire=lambda: None, release=lambda: None)
    workflow = Workflow(workflow_id="wf")
    workflow.add(_Ok("Use"), requires=(resource,))
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    workflow.run( journal=config)

    records = _records(config.path)
    ids = {r["task_id"] for r in records}
    assert any(i.endswith("acquire-db") for i in ids)
    assert any(i.endswith("release-db") for i in ids)
    assert [r["status"] for r in records if r["task_id"].endswith("release-db")] == [
        "started",
        "passed",
    ]


def test_started_record_is_durable_before_task_body_runs(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    seen: dict[str, list[dict]] = {}

    class _Peek(Task[None]):
        title = "Peek"

        def run(self) -> TaskOutcome[None]:
            # The started record must already be flushed to disk mid-task.
            seen["records"] = _records(config.path)
            return TaskOutcome()

    workflow = Workflow(workflow_id="wf")
    workflow.add(_Peek())
    workflow.run( journal=config)

    assert [r["status"] for r in seen["records"]] == ["started"]


def test_release_failure_records_failed_outcome(tmp_path: Path) -> None:
    def _boom() -> None:
        raise RuntimeError("release boom")

    resource = Resource(title="Acquire db", acquire=lambda: None, release=_boom)
    workflow = Workflow(workflow_id="wf")
    workflow.add(_Ok("Use"), requires=(resource,))
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    with pytest.raises(RuntimeError, match="Cleanup failed"):
        workflow.run( journal=config)

    records = _records(config.path)
    release_records = [r for r in records if r["task_id"].endswith("release-db")]
    assert [r["status"] for r in release_records] == ["started", "failed"]


def test_load_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    workflow = Workflow(workflow_id="wf")
    workflow.add(_Ok("Build"))
    config = JournalConfig(path=path)
    workflow.run( journal=config)

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n")  # blank line between attempts, must not break parsing

    # A fresh Journal (via a second run_compiled) re-reads the file including the
    # blank line; a second, ordinary (non-reusable) task run must still succeed.
    workflow.run( journal=config)
    records = _records(path)
    assert max(r["attempt"] for r in records) == 2


def test_load_filters_records_by_workflow_id(tmp_path: Path) -> None:
    """A `passed`+verified-evidence record from a DIFFERENT workflow sharing the same
    journal file must never cause an incorrect skip -- that's the actual danger the
    `workflow_id` filter in `_load()` protects against (an ordinary, non-reusable task
    always reruns regardless, so the scenario needs a reusable task to be meaningful)."""
    path = tmp_path / "journal.jsonl"
    config = JournalConfig(path=path)
    evidence = (Evidence("exact-value", "v1"),)

    other = Workflow(workflow_id="other-workflow")
    other.add(_ReusableOk("Build", evidence=evidence))
    other.run( journal=config)  # records "001.build" passed

    task = _ReusableOk("Build", evidence=evidence)
    workflow = Workflow(workflow_id="wf")
    workflow.add(task)
    workflow.run( journal=config, resume=True)

    assert task.ran is True  # not skipped based on the other workflow's evidence


def test_no_journal_writes_no_file(tmp_path: Path) -> None:
    workflow = Workflow(workflow_id="wf")
    workflow.add(_Ok("Build"))
    path = tmp_path / "journal.jsonl"

    workflow.run()  # journal is optional

    assert not path.exists()
