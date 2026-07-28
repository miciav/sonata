from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sonata_engine.core.compiled import TaskExecution
    from sonata_engine.core.task import Task


class _StepScopeProtocol(Protocol):
    """What a composite is given so it can run its steps without knowing where
    it sits.

    The runner builds it, so it holds the compiled unit id, the journal and the
    resume flag — none of which a task is told. A composite passes a step and a
    slug; the scope names it, decides, records, reports and runs it.

    Private (leading underscore) and exported from nowhere: `TaskInputs._step_scope`,
    the field this types, is itself underscore-private engine plumbing that a
    hand-written task is not meant to reach for. There is no public field to type
    against, so there is nothing to gain from making this Protocol public.
    """

    def run_step(self, step: Task[Any], slug: str, upstream: Any) -> TaskExecution:  # noqa: ANN401
        """Run one step beneath this scope and return how it went."""
        ...
