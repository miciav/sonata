"""Registry for the sink and context that reporting helpers resolve against.

Each value is held twice: a `ContextVar` gives every thread and async task its
own binding, and a module-level fallback keeps the value visible to worker
threads, which start with no contextvars context of their own. The fallback is
what makes reporting from a task's worker thread land under the right task.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sonata_engine.workflow.events import WorkflowContext, WorkflowSink

_workflow_sink_var: ContextVar[WorkflowSink | None] = ContextVar(
    "sonata_engine_sink",
    default=None,
)
_workflow_sink_shared: WorkflowSink | None = None
_workflow_context_var: ContextVar[WorkflowContext | None] = ContextVar(
    "sonata_engine_context",
    default=None,
)
_workflow_context_shared: WorkflowContext | None = None


@contextmanager
def bind_workflow_sink(sink: WorkflowSink) -> Generator[None, None, None]:
    """Route workflow events to `sink` for the duration of the block.

    Binds `sink` both as the current contextvar and as the shared fallback, so
    worker threads spawned inside the block report to it too. The previous
    bindings are restored on exit.
    """
    global _workflow_sink_shared
    previous = _workflow_sink_shared
    _workflow_sink_shared = sink
    token = _workflow_sink_var.set(sink)
    try:
        yield
    finally:
        _workflow_sink_var.reset(token)
        _workflow_sink_shared = previous


@contextmanager
def bind_workflow_context(context: WorkflowContext) -> Generator[None, None, None]:
    """Make `context` the active workflow context for the duration of the block.

    Like `bind_workflow_sink`, the binding is installed both as a contextvar
    and as the shared fallback. The previous bindings are restored on exit, so
    nested binds unwind as a stack.
    """
    global _workflow_context_shared
    previous = _workflow_context_shared
    _workflow_context_shared = context
    token = _workflow_context_var.set(context)
    try:
        yield
    finally:
        _workflow_context_var.reset(token)
        _workflow_context_shared = previous


def active_sink() -> WorkflowSink | None:
    """Return the currently bound sink, or `None` when none is bound."""
    return _workflow_sink_var.get() or _workflow_sink_shared


def get_workflow_context() -> WorkflowContext | None:
    """Return the active workflow context, or `None` when none is bound."""
    return _workflow_context_var.get() or _workflow_context_shared


def has_workflow_sink() -> bool:
    """Return whether a workflow sink is currently bound."""
    return active_sink() is not None
