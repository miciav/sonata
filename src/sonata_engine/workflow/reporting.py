from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sonata_engine.workflow.context import (
    active_sink,
    bind_workflow_context,
    get_workflow_context,
)
from sonata_engine.workflow.event_builders import (
    build_log_event,
    build_task_event,
)
from sonata_engine.workflow.events import WorkflowContext, WorkflowEvent


def _emit(event: WorkflowEvent) -> None:
    sink = active_sink()
    if sink is not None:
        sink.emit(event)


def workflow_log(
    message: str, *, stream: str = "stdout", context: WorkflowContext | None = None
) -> None:
    _emit(build_log_event(line=message, stream=stream, context=context or get_workflow_context()))


@contextmanager
def status(label: str) -> Generator[None, None, None]:
    sink = active_sink()
    if sink is not None:
        with sink.status(label):
            yield
    else:
        yield


def _child_context(
    *, task_id: str, parent_task_id: str | None, context: WorkflowContext | None
) -> WorkflowContext:
    active = context or get_workflow_context() or WorkflowContext()
    resolved_parent = parent_task_id
    if resolved_parent is None:
        resolved_parent = active.task_id or active.parent_task_id
    return WorkflowContext(
        flow_id=active.flow_id,
        flow_run_id=active.flow_run_id,
        task_id=task_id,
        parent_task_id=resolved_parent,
        task_run_id=active.task_run_id,
    )


@contextmanager
def _task_lifecycle(
    *, task_id: str, title: str = "", context: WorkflowContext | None = None
) -> Generator[WorkflowContext, None, None]:
    """Emit `task.started` on entry, then `task.passed` or `task.failed` on exit.

    Used by the compiled-task runner: identity for a `CompiledTask` is owned by
    the engine, never by the task's own `run()` body. A task that wants to
    report steps it performs itself uses `subtask`, which nests under this one.
    """
    child = _child_context(task_id=task_id, parent_task_id=None, context=context)
    _emit(
        build_task_event(
            kind="task.started",
            task_id=task_id,
            parent_task_id=child.parent_task_id,
            title=title,
            context=child,
        )
    )
    with bind_workflow_context(child):
        try:
            yield child
        except BaseException as exc:
            try:
                _emit(
                    build_task_event(
                        kind="task.failed",
                        task_id=task_id,
                        parent_task_id=child.parent_task_id,
                        title=title,
                        detail=str(exc),
                        context=child,
                    )
                )
            except BaseException as reporting_error:
                exc.add_note(f"Failed to emit task.failed for {task_id}: {reporting_error}")
            raise
        else:
            _emit(
                build_task_event(
                    kind="task.passed",
                    task_id=task_id,
                    parent_task_id=child.parent_task_id,
                    title=title,
                    context=child,
                )
            )


@contextmanager
def subtask(*, task_id: str, title: str = "") -> Generator[None, None, None]:
    """Report one step performed inside a task's own `run()`.

    Emits the same events a compiled unit does, nested under whichever task is
    currently running — the parent comes from the bound context, so a task never
    has to know the id the compiler gave it.

    Subtasks are a reporting concern only. They get no compiler-assigned
    identity, take no part in selection, and are not journalled: the enclosing
    unit stays the unit of work, and a resumed step restarts from its beginning.

    `task_id` is the caller's to choose and must be unique within the run. The
    compiler owns `NNN.slug` and a caller must not mint ids in that shape; build
    one from what the task itself knows instead, which reads well as its own
    name extended by the step: `build-images/cp`.
    """
    with _task_lifecycle(task_id=task_id, title=title):
        yield


def _task_skipped(
    *, task_id: str, title: str = "", context: WorkflowContext | None = None
) -> None:
    """Emit the runner-owned terminal event for a task that was not executed."""
    child = _child_context(task_id=task_id, parent_task_id=None, context=context)
    _emit(
        build_task_event(
            kind="task.skipped",
            task_id=task_id,
            parent_task_id=child.parent_task_id,
            title=title,
            context=child,
        )
    )
