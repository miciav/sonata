from __future__ import annotations

import pytest

from sonata_engine import (
    ReusableTask,
    Selection,
    Steps,
    Task,
    TaskInputs,
    TaskOutcome,
    Workflow,
)
from sonata_engine.errors import InvalidTaskOutcomeError, NoUpstreamValueError


class _Produce(Task[str]):
    def __init__(self, title: str, value: str) -> None:
        self.title = title
        self._value = value

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value=self._value)


class _Forward(Task[str]):
    title = "Forward"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value=inputs.upstream())


class _Decorate(Task[str]):
    title = "Decorate"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value=f"http://{inputs.upstream()}.svc")


def _run(*steps: Task[object], title: str = "Deploy") -> object:
    workflow = Workflow(workflow_id="w")
    workflow.add(Steps(title=title, steps=tuple(steps)))
    execution = workflow.run().tasks[0]
    assert execution.outcome is not None
    return execution.outcome.value


def test_a_value_flows_through_the_pipeline() -> None:
    assert _run(_Produce("Install", "rel-42"), _Forward(), _Decorate()) == "http://rel-42.svc"


def test_none_flows_as_a_legitimate_value() -> None:
    class ProduceNone(Task[None]):
        title = "Produce none"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return TaskOutcome()

    class AssertNone(Task[str]):
        title = "Assert none"

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            assert inputs.upstream() is None
            return TaskOutcome(value="ok")

    assert _run(ProduceNone(), AssertNone()) == "ok"


def test_upstream_raises_in_the_first_step_of_a_top_level_composite() -> None:
    with pytest.raises(NoUpstreamValueError):
        _run(_Forward())


def test_a_nested_composite_receives_the_outer_upstream() -> None:
    """Composition is transparent: the inner pipeline continues the outer one."""
    inner = Steps(title="Inner", steps=(_Forward(),))
    assert _run(_Produce("Install", "rel-42"), inner) == "rel-42"


def test_a_composite_compiles_to_one_unit_and_selection_keeps_it_whole() -> None:
    workflow = Workflow(workflow_id="w")
    workflow.add(Steps(title="Deploy", steps=(_Produce("Install", "rel-42"), _Decorate())))

    compiled = workflow.compile()
    assert [task.task_id for task in compiled.tasks] == ["001.deploy"]

    selected = workflow.compile(select=Selection(only="deploy"))
    assert [task.task_id for task in selected.tasks] == ["001.deploy"]


def test_an_already_written_task_serves_as_a_step_unmodified() -> None:
    """The central promise: nothing about Task changed to make this work."""

    class WrittenBefore(Task[str]):
        title = "Written before"

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            return TaskOutcome(value="unchanged")

    assert _run(WrittenBefore()) == "unchanged"


def test_construction_rejects_an_empty_step_list() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        Steps(title="Deploy", steps=())


def test_construction_rejects_duplicate_normalized_slugs() -> None:
    """'Build A' and 'Build-A' normalize to the same journal identity."""
    with pytest.raises(ValueError, match="duplicate step slug 'build-a'"):
        Steps(title="Deploy", steps=(_Produce("Build A", "1"), _Produce("Build-A", "2")))


def test_construction_rejects_a_title_that_normalizes_to_nothing() -> None:
    with pytest.raises(ValueError, match="produces an empty slug"):
        Steps(title="Deploy", steps=(_Produce("---", "1"),))


def test_a_step_returning_a_non_outcome_raises() -> None:
    class Bad(Task[None]):
        title = "Bad"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return "not-an-outcome"  # type: ignore[return-value]

    with pytest.raises(InvalidTaskOutcomeError):
        _run(Bad())


def test_a_reusable_step_returning_a_value_raises() -> None:
    """Inherited from the shared executor, not restated in Steps."""

    class BadReusable(ReusableTask):
        title = "Bad reusable"
        reuse_key = "bad"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return TaskOutcome(value=42)  # type: ignore[arg-type]

    with pytest.raises(InvalidTaskOutcomeError):
        _run(BadReusable())


def test_steps_is_idempotent_so_a_failed_unit_can_be_re_entered() -> None:
    assert Steps(title="Deploy", steps=(_Forward(),)).idempotent is True


def test_the_fingerprint_payload_covers_the_steps() -> None:
    def payload(*steps: Task[object]) -> object:
        return Steps(title="Deploy", steps=tuple(steps))._fingerprint_payload()

    class OtherProduce(Task[str]):
        title = "Install"

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            return TaskOutcome(value="1")

    class Reusable(ReusableTask):
        title = "Build"

        def __init__(self, key: str) -> None:
            self._key = key

        @property
        def reuse_key(self) -> str:
            return self._key

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return TaskOutcome()

    a = _Produce("Install", "1")
    b = _Produce("Resolve", "2")
    assert payload(a, b) != payload(b, a)
    assert payload(a) != payload(a, b)
    assert payload(_Produce("Install", "1")) != payload(_Produce("Resolve", "1"))
    assert payload(_Produce("Install", "1")) != payload(OtherProduce())
    assert payload(Reusable("key-1")) != payload(Reusable("key-2"))

    nested_a = Steps(title="Inner", steps=(_Produce("Install", "1"),))
    nested_b = Steps(title="Inner", steps=(_Produce("Resolve", "1"),))
    assert payload(nested_a) != payload(nested_b)
