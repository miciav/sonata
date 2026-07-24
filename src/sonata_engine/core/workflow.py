from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sonata_engine.core.compiled import CompiledTask, CompiledWorkflow
from sonata_engine.core.resource_task import ResourceTask
from sonata_engine.core.task import Task
from sonata_engine.workflow.reporting import workflow_step

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    return _SLUG_INVALID_CHARS.sub("-", title.lower()).strip("-")


@dataclass
class Workflow:
    """Sequential task executor with optional always-run cleanup tasks, plus a
    task-definition builder/compiler.

    tasks run in order; execution stops at the first failure.
    cleanup_tasks always run, even after a failure in tasks.

    `add()`/`compile()` are a separate, coexisting concern: they record ordered
    `Task` definitions and turn them into an immutable `CompiledWorkflow` with
    compiler-assigned IDs. Task objects never carry their own ID.
    """

    # ponytail: `tasks`/`cleanup_tasks` accept duck-typed executor steps (e.g. ResourceTask),
    # not only `Task` instances -- typed `Any` rather than `Task` to match that real, pre-existing
    # contract. Tighten once the old executor is folded onto the compiled model (Tasks 4/5).
    tasks: list[Any]
    # ponytail: default "" (not a required field) so untouched callers that never compile()
    # keep working; compile() fails loud on an empty workflow_id instead.
    workflow_id: str = ""
    cleanup_tasks: list[Any] = field(default_factory=list)
    keep_infrastructure: bool = False
    _definitions: list[Task[Any]] = field(default_factory=list, init=False, repr=False)

    @property
    def task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks + self.cleanup_tasks]

    @property
    def phase_titles(self) -> list[str]:
        return [t.title for t in self.tasks + self.cleanup_tasks]

    def run(self) -> None:
        main_error: BaseException | None = None
        acquired_resources: list[ResourceTask] = []

        for task in self.tasks:
            try:
                with workflow_step(task_id=task.task_id, title=task.title):
                    task.run()
                if isinstance(task, ResourceTask):
                    acquired_resources.append(task)
            except BaseException as exc:
                main_error = exc
                break

        cleanup_errors: list[str] = []
        if not self.keep_infrastructure:
            for task in self.cleanup_tasks:
                try:
                    with workflow_step(task_id=task.task_id, title=task.title):
                        task.run()
                except Exception as exc:
                    cleanup_errors.append(str(exc))

        for resource in reversed(acquired_resources):
            if self.keep_infrastructure and resource.infrastructure:
                continue
            try:
                with workflow_step(
                    task_id=resource.cleanup_task_id,
                    title=resource.cleanup_title,
                ):
                    resource.cleanup()
            except Exception as exc:
                cleanup_errors.append(str(exc))

        if main_error is not None:
            if cleanup_errors:
                combined = f"{main_error}\n\nCleanup errors:\n" + "\n".join(cleanup_errors)
                raise RuntimeError(combined) from main_error
            raise main_error

        if cleanup_errors:
            raise RuntimeError("Cleanup failed:\n" + "\n".join(cleanup_errors))

    def add(self, task: Task[Any]) -> Workflow:
        """Record a task definition. Ordering is preserved for `compile()`."""
        self._definitions.append(task)
        return self

    def compile(self) -> CompiledWorkflow:
        """Assign stable, deterministic IDs to the recorded task definitions.

        IDs are `{ordinal:03d}.{slug}`, derived from insertion order and the
        task's title; duplicate titles are disambiguated by ordinal. The
        result is immutable.
        """
        if not self.workflow_id:
            raise ValueError("Workflow.compile() requires a non-empty workflow_id")

        compiled_tasks = tuple(
            CompiledTask(task_id=f"{ordinal:03d}.{_slugify(task.title)}", task=task)
            for ordinal, task in enumerate(self._definitions, start=1)
        )
        return CompiledWorkflow(workflow_id=self.workflow_id, tasks=compiled_tasks)
