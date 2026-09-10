"""Composites assembled from smaller tasks rather than written as one body."""

from __future__ import annotations

from typing import Any, override

from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.slug import slugify
from sonata_engine.core.task import Task
from sonata_engine.errors import StepScopeUnavailableError


class Steps(Task[Any]):
    """A task assembled from steps rather than written.

    Each step is an ordinary `Task`, run in order, on this task's own thread.
    Each receives the value the step before it produced, so a pipeline needs no
    wiring; a step that needs nothing simply never asks. The whole thing stays
    one compiled unit: one ordinal, one thing `Selection` can name, one fate.

    Steps are journalled individually, so a resumed composite skips the steps it
    already finished. What may be skipped is decided exactly as it is for a
    compiled unit — a `ReusableTask` whose evidence still verifies. Its only
    legal value is None, which is reconstructed when the step is skipped.

    `idempotent` is always `True`. It says this coordinator is safe to re-enter
    after a failed attempt, and says nothing about the steps: each carries its
    own flag. Without it the enclosing unit's failed record would refuse the
    resume this class exists to make cheap.
    """

    idempotent = True

    def __init__(self, *, title: str, steps: tuple[Task[Any], ...]) -> None:
        """Assemble `steps` into one composite named `title`.

        Each step is slugged from its own title to give it a stable name inside
        the composite. An empty `steps` sequence, a title that slugs to nothing,
        or two steps that slug the same are all rejected here, since the slug is
        what the journal keys the step's records on.
        """
        if not steps:
            raise ValueError("Steps requires at least one step")

        slugs: list[str] = []
        for step in steps:
            slug = slugify(step.title)
            if not slug:
                raise ValueError(f"step title {step.title!r} produces an empty slug")
            if slug in slugs:
                raise ValueError(f"duplicate step slug {slug!r} in {title!r}")
            slugs.append(slug)

        self.title = title
        self._steps = steps
        self._slugs = tuple(slugs)

    @override
    def _fingerprint_payload(self) -> object:
        return tuple(
            (
                slug,
                f"{type(step).__module__}.{type(step).__qualname__}",
                step._fingerprint_payload(),
            )
            for slug, step in zip(self._slugs, self._steps, strict=True)
        )

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[Any]:
        scope = inputs._step_scope
        if scope is None:
            raise StepScopeUnavailableError(
                f"{type(self).__name__} {self.title!r} must be run by the workflow "
                "runner: add it to a Workflow rather than calling run() directly"
            )

        upstream: Any = inputs._upstream
        for slug, step in zip(self._slugs, self._steps, strict=True):
            execution = scope.run_step(step, slug, upstream)
            # A step that ran contributes its value, including a legitimate
            # None; a skipped step contributes None, the only value a skippable
            # ReusableTask may return.
            upstream = (
                execution.outcome.value if execution.outcome is not None else None
            )
        return TaskOutcome(value=upstream)
