"""Optional append-only JSON Lines journal (schema v2) and resume-decision logic.

The journal records the lifecycle of a compiled workflow so an interrupted run can
resume. Storage is one JSON object per line; a single logical task (`task_id`) can
own several physical attempt records. Schema v2 stores only generic
workflow/task/attempt/evidence data -- no release identity, semantic versions,
registries, or artifact policy (those do not belong in Sonata).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sonata_engine.core.compiled import CompiledTask, CompiledWorkflow
from sonata_engine.core.outcome import Evidence
from sonata_engine.core.task import ReusableTask
from sonata_engine.errors import (
    AmbiguousTaskStateError,
    CorruptJournalError,
    UnsupportedJournalSchemaError,
    WorkflowTopologyMismatchError,
)

SCHEMA_VERSION = 2

Verifier = Callable[[Evidence], bool]
ResumeAction = Literal["run", "skip"]


# --- Evidence verification registry ------------------------------------------


def _verify_file_digest(evidence: Evidence) -> bool:
    """`file-digest` evidence verifies iff the file at `reference` still hashes to `digest`."""
    if evidence.digest is None:
        return False
    try:
        data = Path(evidence.reference).read_bytes()
    except OSError:
        return False
    expected = evidence.digest.removeprefix("sha256:")
    return hashlib.sha256(data).hexdigest() == expected


DEFAULT_VERIFIERS: dict[str, Verifier] = {
    "file-digest": _verify_file_digest,
}


def _resolve_verifiers(verifiers: Mapping[str, Verifier] | None) -> dict[str, Verifier]:
    """Injected verifiers are MERGED over the built-in defaults (same key overrides)."""
    resolved = dict(DEFAULT_VERIFIERS)
    if verifiers:
        resolved.update(verifiers)
    return resolved


def _all_verified(evidence: tuple[Evidence, ...], verifiers: Mapping[str, Verifier]) -> bool:
    """Every evidence entry must verify. Unknown `kind` (no verifier) fails closed."""
    if not evidence:
        return False
    for entry in evidence:
        verifier = verifiers.get(entry.kind)
        if verifier is None or not verifier(entry):
            return False
    return True


# --- Resume decision ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskState:
    """The latest recorded state of one logical task, folded from its attempt records."""

    task_id: str
    attempt: int
    status: str
    evidence: tuple[Evidence, ...]


def decide_resume(
    prior: TaskState | None,
    *,
    idempotent: bool,
    reusable: bool,
    verifiers: Mapping[str, Verifier],
) -> ResumeAction:
    """Decide whether to run or skip a consumer task given its prior recorded state.

    - No prior record, or a topology-initialized `pending` record: run fresh.
    - Done (passed/skipped): a reusable task whose every evidence entry verifies is
      skipped; anything else (ordinary task, or stale/unverifiable evidence) reruns.
    - Interrupted after `started`, or genuinely `failed`: an idempotent task may retry
      safely; a non-idempotent task cannot be resumed automatically -> raise.
    """
    if prior is None or prior.status == "pending":
        return "run"
    if prior.status in ("passed", "skipped"):
        if reusable and _all_verified(prior.evidence, verifiers):
            return "skip"
        return "run"
    # "started" (no terminal record) or "failed": only idempotent tasks retry.
    if idempotent:
        return "run"
    raise AmbiguousTaskStateError(
        f"{prior.task_id} is {prior.status!r} and non-idempotent; refusing automatic resume"
    )


# --- Storage -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JournalConfig:
    """Where the append-only journal lives."""

    path: Path


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _evidence_to_json(evidence: tuple[Evidence, ...]) -> list[dict[str, str | None]]:
    return [{"kind": e.kind, "reference": e.reference, "digest": e.digest} for e in evidence]


def _evidence_from_json(raw: list[dict[str, str | None]]) -> tuple[Evidence, ...]:
    return tuple(
        Evidence(kind=str(e["kind"]), reference=str(e["reference"]), digest=e.get("digest"))
        for e in raw
    )


class Journal:
    """Append-only JSON Lines reader/writer plus the per-task resume index.

    On construction it reads the whole file once, rejects any foreign
    `schema_version`, and folds the records into one `TaskState` per `task_id`.
    Writes are append-only and flushed+fsynced before the task executes, so a
    crash mid-task leaves a durable `started` record.

    Intended model: one journal file per workflow. `_load()` defensively filters
    records by `workflow_id` in case a file is ever shared across workflows.

    The constructor receives the complete compiled workflow, validates its
    deterministic fingerprint against every existing record, and creates attempt-0
    `pending` records for every task missing from the journal. Resume therefore
    refuses changed topologies instead of reusing evidence under a stale task ID.
    """

    def __init__(
        self,
        config: JournalConfig,
        compiled: CompiledWorkflow,
        verifiers: Mapping[str, Verifier] | None = None,
    ) -> None:
        self.path = config.path
        self.workflow_id = compiled.workflow_id
        self.workflow_fingerprint = compiled.fingerprint
        # ponytail: uuid4 hex, not a ULID -- unique-per-run is all we need, no new dep.
        self.run_id = uuid.uuid4().hex
        self.verifiers = _resolve_verifiers(verifiers)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # started_at captured per attempt so the terminal record can repeat it.
        self._started_at: dict[tuple[str, int], str] = {}
        self._states = self._load()
        for task in compiled.tasks:
            if task.task_id not in self._states:
                self._write(task.task_id, 0, "pending")

    def _load(self) -> dict[str, TaskState]:
        states: dict[str, TaskState] = {}
        if not self.path.exists():
            return states
        lines = self.path.read_bytes().splitlines(keepends=True)
        offset = 0
        for index, raw_line in enumerate(lines):
            line_start = offset
            offset += len(raw_line)
            is_torn_tail = index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r"))
            try:
                line = raw_line.decode("utf-8")
            except UnicodeError as exc:
                if is_torn_tail:
                    self._truncate_torn_tail(line_start)
                    break
                raise CorruptJournalError(
                    f"{self.path}:{index + 1}: record is not valid UTF-8"
                ) from exc
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                if is_torn_tail:
                    self._truncate_torn_tail(line_start)
                    break
                raise CorruptJournalError(
                    f"{self.path}:{index + 1}: malformed JSON record"
                ) from exc
            if not isinstance(record, dict):
                raise CorruptJournalError(
                    f"{self.path}:{index + 1}: journal record must be an object"
                )
            version = record.get("schema_version")
            if version != SCHEMA_VERSION:
                raise UnsupportedJournalSchemaError(
                    f"{self.path}: schema_version {version!r}, expected {SCHEMA_VERSION}"
                )
            if record.get("workflow_id") != self.workflow_id:
                continue
            fingerprint = record.get("workflow_fingerprint")
            if fingerprint != self.workflow_fingerprint:
                raise WorkflowTopologyMismatchError(
                    f"{self.path}: workflow {self.workflow_id!r} has fingerprint "
                    f"{fingerprint!r}, expected {self.workflow_fingerprint!r}"
                )
            try:
                task_id = str(record["task_id"])
                attempt = int(record["attempt"])
                status = str(record["status"])
                raw_evidence = record.get("evidence", [])
                if not isinstance(raw_evidence, list):
                    raise TypeError("evidence must be a list")
                evidence = _evidence_from_json(raw_evidence)
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptJournalError(
                    f"{self.path}:{index + 1}: malformed journal record"
                ) from exc
            if status not in {"pending", "started", "passed", "failed", "skipped"}:
                raise CorruptJournalError(
                    f"{self.path}:{index + 1}: invalid task status {status!r}"
                )
            existing = states.get(task_id)
            # Records are appended in order: a later record for the same (or higher)
            # attempt supersedes an earlier one -- terminal replaces started.
            if existing is None or attempt >= existing.attempt:
                states[task_id] = TaskState(
                    task_id=task_id,
                    attempt=attempt,
                    status=status,
                    evidence=evidence,
                )
        return states

    def _truncate_torn_tail(self, offset: int) -> None:
        """Remove only a non-newline-terminated final record after a crash."""
        with open(self.path, "r+b") as handle:
            handle.truncate(offset)
            handle.flush()
            os.fsync(handle.fileno())

    def next_attempt(self, task_id: str) -> int:
        state = self._states.get(task_id)
        return state.attempt + 1 if state is not None else 1

    def decide(self, compiled_task: CompiledTask[object]) -> ResumeAction:
        """Resume decision for a consumer task (may raise `AmbiguousTaskStateError`)."""
        task = compiled_task.task
        return decide_resume(
            self._states.get(compiled_task.task_id),
            idempotent=task.idempotent,
            reusable=isinstance(task, ReusableTask),
            verifiers=self.verifiers,
        )

    def record_started(self, task_id: str, attempt: int) -> None:
        self._write(task_id, attempt, "started", started_at=_utc_iso(), finished_at=None)

    def record_passed(
        self, task_id: str, attempt: int, evidence: tuple[Evidence, ...] = ()
    ) -> None:
        self._write(task_id, attempt, "passed", finished_at=_utc_iso(), evidence=evidence)

    def record_failed(self, task_id: str, attempt: int) -> None:
        self._write(task_id, attempt, "failed", finished_at=_utc_iso())

    def record_skipped(self, task_id: str, attempt: int) -> None:
        """Record a skip, carrying forward the evidence that justified it."""
        prior = self._states.get(task_id)
        evidence = prior.evidence if prior is not None else ()
        self._write(task_id, attempt, "skipped", finished_at=_utc_iso(), evidence=evidence)

    def _write(
        self,
        task_id: str,
        attempt: int,
        status: str,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        evidence: tuple[Evidence, ...] = (),
    ) -> None:
        if started_at is not None:
            self._started_at[(task_id, attempt)] = started_at
        record = {
            "schema_version": SCHEMA_VERSION,
            "workflow_id": self.workflow_id,
            "workflow_fingerprint": self.workflow_fingerprint,
            "run_id": self.run_id,
            "task_id": task_id,
            "attempt": attempt,
            "status": status,
            "started_at": self._started_at.get((task_id, attempt)),
            "finished_at": finished_at,
            "evidence": _evidence_to_json(evidence),
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            if status == "started":
                # Only the pre-execution record needs disk-level durability: it's the
                # one a crash mid-task must leave behind. Terminal records losing their
                # fsync race just look like `started` on the next resume, which
                # guard_not_ambiguous/decide_resume already handle safely.
                os.fsync(handle.fileno())
        self._states[task_id] = TaskState(task_id, attempt, status, evidence)
