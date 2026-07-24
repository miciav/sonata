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

    Used exclusively by the compiled-task runner, so lifecycle identity for a
    `CompiledTask` is owned by the engine, not by the task's own `run()` body.
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
