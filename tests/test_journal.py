from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from sonata_engine.core.outcome import Evidence, TaskOutcome
from sonata_engine.core.resource_task import Resource
from sonata_engine.core.task import Task
from sonata_engine.core.workflow import Workflow
from sonata_engine.journal import JournalConfig


class _Ok(Task[None]):
    def __init__(self, title: str, evidence: tuple[Evidence, ...] = ()) -> None:
        self.title = title
        self._evidence = evidence

    def run(self) -> TaskOutcome[None]:
        return TaskOutcome(evidence=self._evidence)


class _Boom(Task[None]):
    title = "Boom"

    def run(self) -> TaskOutcome[None]:
        raise RuntimeError("boom")


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _five_task_workflow() -> Workflow:
    workflow = Workflow(tasks=[], workflow_id="release")
    for index in range(5):
        workflow.add(_Ok(f"Task {index}"))
    return workflow


# --- Step 1: topology --------------------------------------------------------


def test_five_tasks_produce_five_logical_entries(tmp_path: Path) -> None:
    workflow = _five_task_workflow()
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    workflow.run_compiled(workflow.compile(), journal=config)

    records = _records(config.path)
    assert len({r["task_id"] for r in records}) == 5
    assert all(r["schema_version"] == 2 for r in records)
    assert len([r for r in records if r["status"] == "started"]) == 5
    assert len([r for r in records if r["status"] == "passed"]) == 5


def test_retries_add_attempts_without_new_logical_task(tmp_path: Path) -> None:
    workflow = _five_task_workflow()
    compiled = workflow.compile()
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    # Ordinary (non-reusable) tasks: a passed task runs again on the next run.
    workflow.run_compiled(compiled, journal=config)
    workflow.run_compiled(compiled, journal=config)

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
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_Ok("Build"))
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    workflow.run_compiled(workflow.compile(), journal=config)

    records = _records(config.path)
    assert [r["status"] for r in records] == ["started", "passed"]
    assert records[0]["started_at"] is not None
    assert records[1]["finished_at"] is not None
    # The terminal record repeats the attempt's started_at.
    assert records[1]["started_at"] == records[0]["started_at"]


def test_records_started_then_failed(tmp_path: Path) -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_Boom())
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    with pytest.raises(RuntimeError, match="boom"):
        workflow.run_compiled(workflow.compile(), journal=config)

    records = _records(config.path)
    assert [r["status"] for r in records] == ["started", "failed"]


def test_records_finalizer_outcomes(tmp_path: Path) -> None:
    resource = Resource(title="Acquire db", acquire=lambda: None, release=lambda: None)
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_Ok("Use"), requires=(resource,))
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    workflow.run_compiled(workflow.compile(), journal=config)

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

    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_Peek())
    workflow.run_compiled(workflow.compile(), journal=config)

    assert [r["status"] for r in seen["records"]] == ["started"]


def test_no_journal_writes_no_file(tmp_path: Path) -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_Ok("Build"))
    path = tmp_path / "journal.jsonl"

    workflow.run_compiled(workflow.compile())  # journal is optional

    assert not path.exists()
