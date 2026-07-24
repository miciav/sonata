from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sonata_engine.core.outcome import Evidence, TaskOutcome
from sonata_engine.core.task import ReusableTask, Task
from sonata_engine.core.workflow import Workflow
from sonata_engine.errors import (
    AmbiguousTaskStateError,
    ResumeConfigurationError,
    UnsupportedJournalSchemaError,
)
from sonata_engine.journal import JournalConfig


class _Tracker(Task[None]):
    def __init__(
        self, title: str, *, idempotent: bool = False, evidence: tuple[Evidence, ...] = ()
    ) -> None:
        self.title = title
        self.idempotent = idempotent
        self._evidence = evidence
        self.ran = False

    def run(self) -> TaskOutcome[None]:
        self.ran = True
        return TaskOutcome(evidence=self._evidence)


class _ReusableTracker(ReusableTask):
    def __init__(self, title: str, evidence: tuple[Evidence, ...] = ()) -> None:
        self.title = title
        self._evidence = evidence
        self.ran = False

    def run(self) -> TaskOutcome[None]:
        self.ran = True
        return TaskOutcome(evidence=self._evidence)


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _seed(
    path: Path,
    task_id: str,
    status: str,
    *,
    attempt: int = 1,
    evidence: tuple[Evidence, ...] = (),
    workflow_id: str = "wf",
    schema_version: int = 2,
) -> None:
    record = {
        "schema_version": schema_version,
        "workflow_id": workflow_id,
        "run_id": "seed",
        "task_id": task_id,
        "attempt": attempt,
        "status": status,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": None if status == "started" else "2026-01-01T00:00:01Z",
        "evidence": [
            {"kind": e.kind, "reference": e.reference, "digest": e.digest} for e in evidence
        ],
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _run(task: Task[None], config: JournalConfig, **kwargs: object) -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(task)
    workflow.run_compiled(workflow.compile(), journal=config, **kwargs)  # type: ignore[arg-type]


# --- Resume matrix -----------------------------------------------------------


def test_passed_valid_evidence_reusable_skips(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "passed", evidence=(Evidence("exact-value", "v1.2.3"),))
    task = _ReusableTracker("Build", evidence=(Evidence("exact-value", "v1.2.3"),))

    _run(task, config, resume=True)

    assert task.ran is False
    assert _records(config.path)[-1]["status"] == "skipped"


def test_passed_valid_file_digest_reusable_skips(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("stable")
    digest = "sha256:" + hashlib.sha256(b"stable").hexdigest()
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(
        config.path,
        "001.build",
        "passed",
        evidence=(Evidence("file-digest", str(artifact), digest),),
    )
    task = _ReusableTracker("Build")

    _run(task, config, resume=True)

    assert task.ran is False


def test_passed_stale_evidence_reusable_runs(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("original")
    digest = "sha256:" + hashlib.sha256(b"original").hexdigest()
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(
        config.path,
        "001.build",
        "passed",
        evidence=(Evidence("file-digest", str(artifact), digest),),
    )
    artifact.write_text("mutated")  # digest no longer matches -> stale
    task = _ReusableTracker("Build")

    _run(task, config, resume=True)

    assert task.ran is True


def test_passed_ordinary_task_runs(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "passed", evidence=(Evidence("exact-value", "v1"),))
    task = _Tracker("Build")

    _run(task, config, resume=True)

    assert task.ran is True


def test_started_only_idempotent_retries(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "started")
    task = _Tracker("Build", idempotent=True)

    _run(task, config, resume=True)

    assert task.ran is True
    assert max(r["attempt"] for r in _records(config.path)) == 2


def test_started_only_non_idempotent_raises(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "started")
    task = _Tracker("Build", idempotent=False)

    with pytest.raises(AmbiguousTaskStateError):
        _run(task, config, resume=True)

    assert task.ran is False


def test_failed_non_idempotent_no_automatic_retry(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "failed")
    task = _Tracker("Build", idempotent=False)

    with pytest.raises(AmbiguousTaskStateError):
        _run(task, config, resume=True)

    assert task.ran is False


def test_failed_idempotent_retries(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "failed")
    task = _Tracker("Build", idempotent=True)

    _run(task, config, resume=True)

    assert task.ran is True


# --- Evidence verifier registry ----------------------------------------------


def test_unknown_evidence_kind_fails_closed_and_runs(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "passed", evidence=(Evidence("mystery-kind", "x"),))
    task = _ReusableTracker("Build")

    _run(task, config, resume=True)

    assert task.ran is True  # unverifiable evidence never skips


def test_injected_verifier_enables_skip(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "passed", evidence=(Evidence("custom", "ok"),))
    task = _ReusableTracker("Build")

    _run(task, config, resume=True, verifiers={"custom": lambda _e: True})

    assert task.ran is False


# --- Configuration / schema guards -------------------------------------------


def test_resume_without_journal_raises() -> None:
    workflow = Workflow(tasks=[], workflow_id="wf")
    workflow.add(_Tracker("Build"))

    with pytest.raises(ResumeConfigurationError):
        workflow.run_compiled(workflow.compile(), resume=True)


def test_schema_v1_is_rejected(tmp_path: Path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    _seed(config.path, "001.build", "passed", schema_version=1)
    task = _Tracker("Build")

    with pytest.raises(UnsupportedJournalSchemaError):
        _run(task, config, resume=True)
