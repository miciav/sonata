from __future__ import annotations

import pytest

from sonata_engine import (
    Evidence,
    JournalConfig,
    ReusableTask,
    Steps,
    Task,
    TaskInputs,
    TaskOutcome,
    Workflow,
)
from sonata_engine.errors import AmbiguousTaskStateError, WorkflowTopologyMismatchError
from sonata_engine.journal import Journal

ALWAYS = {"always": lambda _e: True}


class _Reusable(ReusableTask):
    """Verified evidence lets resume skip it and reconstruct its value as None."""

    def __init__(self, title: str, ran: list[str]) -> None:
        self.title = title
        self._ran = ran

    @property
    def reuse_key(self) -> str:
        return self.title

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        self._ran.append(self.title)
        return TaskOutcome(evidence=(Evidence("always", self.title),))


class _RetryableBuild(ReusableTask):
    title = "Build app"
    idempotent = True
    reuse_key = "build-app-v1"

    def __init__(self, ran: list[str], attempts: list[int]) -> None:
        self._ran = ran
        self._attempts = attempts

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        # On the resumed run the preceding reusable step is skipped. Its only
        # legal value, None, must still be reconstructed as this step's upstream.
        assert inputs.upstream() is None
        self._ran.append(self.title)
        self._attempts[0] += 1
        if self._attempts[0] == 1:
            raise RuntimeError("boom")
        return TaskOutcome(evidence=(Evidence("always", self.title),))


def _workflow(ran: list[str], attempts: list[int]) -> Workflow:
    workflow = Workflow(workflow_id="w")
    workflow.add(
        Steps(
            title="Publish",
            steps=(
                _Reusable("Build cp", ran),
                _Reusable("Build fn", ran),
                _RetryableBuild(ran, attempts),
            ),
        )
    )
    return workflow


def test_resume_skips_finished_reusable_steps_and_retries_the_failed_one(
    tmp_path,
) -> None:
    """Assert resume skips finished reusable steps and retries the failed one.

    The point of the journal work: the last build fails, you resume, the
    earlier builds do not run again.
    """
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    ran: list[str] = []
    attempts = [0]

    with pytest.raises(RuntimeError, match="boom"):
        _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)
    assert ran == ["Build cp", "Build fn", "Build app"]

    ran.clear()
    _workflow(ran, attempts).run(journal=config, resume=True, verifiers=ALWAYS)
    assert ran == ["Build app"]
    assert attempts == [2]


def test_resume_false_runs_every_step_even_with_a_journal(tmp_path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    ran: list[str] = []
    attempts = [1]  # the retryable build succeeds on every run in this test
    _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)
    assert ran == ["Build cp", "Build fn", "Build app"]

    ran.clear()
    _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)
    assert ran == ["Build cp", "Build fn", "Build app"]


def test_an_interrupted_non_idempotent_step_refuses_to_resume(tmp_path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    class Fragile(Task[str]):
        title = "Fragile"
        idempotent = False

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            raise AssertionError("an interrupted non-idempotent step must not run")

    def build() -> Workflow:
        workflow = Workflow(workflow_id="w")
        workflow.add(Steps(title="Publish", steps=(Fragile(),)))
        return workflow

    # Simulate a process stopping after the durable child `started` record but
    # before any terminal record. The enclosing unit remains `pending`, so the
    # resume reaches the child decision.
    seeded = build()
    Journal(config, seeded.compile()).record_started("001.publish/fragile", 1)

    with pytest.raises(AmbiguousTaskStateError):
        build().run(journal=config, resume=True, verifiers=ALWAYS)


def test_changing_the_step_list_invalidates_resume(tmp_path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    ran: list[str] = []
    attempts = [0]
    with pytest.raises(RuntimeError, match="boom"):
        _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)

    changed = Workflow(workflow_id="w")
    changed.add(
        Steps(
            title="Publish",
            steps=(
                _Reusable("Build fn", ran),
                _Reusable("Build cp", ran),
                _RetryableBuild(ran, attempts),
            ),
        )
    )
    with pytest.raises(WorkflowTopologyMismatchError):
        changed.run(journal=config, resume=True, verifiers=ALWAYS)
