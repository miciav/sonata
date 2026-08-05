from __future__ import annotations

from sonata_engine.workflow.events import WorkflowContext, WorkflowEvent


def _resolve_context_fields(
    *,
    flow_id: str | None,
    flow_run_id: str | None,
    task_id: str | None,
    parent_task_id: str | None,
    task_run_id: str | None,
    context: WorkflowContext | None,
    inherit_task_id: bool = True,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    active = context or WorkflowContext()
    if task_id is not None:
        resolved_task_id = task_id
    elif inherit_task_id:
        resolved_task_id = active.task_id
    else:
        resolved_task_id = None
    resolved_parent = parent_task_id if parent_task_id is not None else active.parent_task_id
    return (
        flow_id or active.flow_id,
        flow_run_id or active.flow_run_id,
        resolved_task_id,
        resolved_parent,
        task_run_id or active.task_run_id,
    )


def build_task_event(
    *,
    kind: str,
    flow_id: str | None = None,
    flow_run_id: str | None = None,
    task_id: str | None = None,
    parent_task_id: str | None = None,
    task_run_id: str | None = None,
    title: str = "",
    detail: str = "",
    context: WorkflowContext | None = None,
) -> WorkflowEvent:
    resolved = _resolve_context_fields(
        flow_id=flow_id,
        flow_run_id=flow_run_id,
        task_id=task_id,
        parent_task_id=parent_task_id,
        task_run_id=task_run_id,
        context=context,
    )
    return WorkflowEvent(
        kind=kind,
        flow_id=resolved[0],
        flow_run_id=resolved[1],
        task_id=resolved[2],
        parent_task_id=resolved[3],
        task_run_id=resolved[4],
        title=title or resolved[2] or kind,
        detail=detail,
    )


def build_log_event(
    *,
    line: str,
    flow_id: str | None = None,
    flow_run_id: str | None = None,
    task_id: str | None = None,
    parent_task_id: str | None = None,
    task_run_id: str | None = None,
    stream: str = "stdout",
    context: WorkflowContext | None = None,
) -> WorkflowEvent:
    resolved = _resolve_context_fields(
        flow_id=flow_id,
        flow_run_id=flow_run_id,
        task_id=task_id,
        parent_task_id=parent_task_id,
        task_run_id=task_run_id,
        context=context,
    )
    return WorkflowEvent(
        kind="log.line",
        flow_id=resolved[0],
        flow_run_id=resolved[1],
        task_id=resolved[2],
        parent_task_id=resolved[3],
        task_run_id=resolved[4],
        stream=stream,
        line=line,
    )
