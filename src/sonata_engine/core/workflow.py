from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sonata_engine.core.compiled import CompiledTask, CompiledWorkflow
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.resource_task import Resource, ResourceOp
from sonata_engine.core.task import Task
from sonata_engine.workflow.reporting import task_lifecycle, workflow_step

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


class InvalidTaskOutcomeError(Exception):
    """Raised when a compiled task's `run()` returns something other than `TaskOutcome`."""


def _slugify(title: str) -> str:
    return _SLUG_INVALID_CHARS.sub("-", title.lower()).strip("-")


@dataclass
class Workflow:
    """Sequential task executor with optional always-run cleanup tasks, plus a
    task-definition builder/compiler.

    tasks run in order; execution stops at the first failure.
    cleanup_tasks always run, even after a failure in tasks.

    `add()`/`compile()`/`run_compiled()` are a separate, coexisting concern:
    they record ordered `Task` definitions (with the `Resource`s each consumes)
    and turn them into an immutable `CompiledWorkflow` with compiler-assigned
    IDs. Task objects never carry their own ID.
    """

    # ponytail: `tasks`/`cleanup_tasks` accept duck-typed executor steps, not only `Task`
    # instances -- typed `Any` rather than `Task` to match that real, pre-existing contract.
    tasks: list[Any]
    # ponytail: default "" (not a required field) so untouched callers that never compile()
    # keep working; compile() fails loud on an empty workflow_id instead.
    workflow_id: str = ""
    cleanup_tasks: list[Any] = field(default_factory=list)
    keep_infrastructure: bool = False
    _definitions: list[tuple[Task[Any], tuple[Resource, ...]]] = field(
        default_factory=list, init=False, repr=False
    )

    @property
    def task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks + self.cleanup_tasks]

    @property
    def phase_titles(self) -> list[str]:
        return [t.title for t in self.tasks + self.cleanup_tasks]

    def run(self) -> None:
        main_error: BaseException | None = None

        for task in self.tasks:
            try:
                with workflow_step(task_id=task.task_id, title=task.title):
                    task.run()
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

        if main_error is not None:
            if cleanup_errors:
                combined = f"{main_error}\n\nCleanup errors:\n" + "\n".join(cleanup_errors)
                raise RuntimeError(combined) from main_error
            raise main_error

        if cleanup_errors:
            raise RuntimeError("Cleanup failed:\n" + "\n".join(cleanup_errors))

    def run_compiled(self, compiled: CompiledWorkflow) -> None:
        """Run a `CompiledWorkflow`'s tasks in order, owning their lifecycle events.

        Happy path: acquire/consumer/release units execute in the linear order
        `compile()` placed them -- releases already sit after their last
        consumer, so no special-casing is needed.

        Failure path: the invariant "release always runs after acquisition,
        including after a consumer or acquire failure" cannot hold from linear
        order alone, because a release unit is only *reached* later in the
        sequence. So when a consumer or acquire raises, we stop the main walk
        and run the release unit for every resource acquired-but-not-yet-released,
        in reverse acquisition order. Release errors never abort remaining
        releases; they are collected and combined with the primary error.

        `keep_infrastructure` skips a release only when its `Resource` is
        `infrastructure` -- safety resources are always released.
        """
        release_for = {ct.resource: ct for ct in compiled.tasks if ct.kind == "release"}
        # Release units for acquired-but-not-yet-released resources, in acquisition order.
        pending: list[CompiledTask[object]] = []
        release_errors: list[str] = []
        main_error: BaseException | None = None

        for compiled_task in compiled.tasks:
            if compiled_task.kind == "release":
                self._release(compiled_task, release_errors)
                pending = [p for p in pending if p is not compiled_task]
                continue
            try:
                with task_lifecycle(task_id=compiled_task.task_id, title=compiled_task.task.title):
                    outcome = compiled_task.task.run()
                    if not isinstance(outcome, TaskOutcome):
                        raise InvalidTaskOutcomeError(
                            f"{compiled_task.task_id} returned {outcome!r}, expected TaskOutcome"
                        )
            except BaseException as exc:
                main_error = exc
                break
            if compiled_task.kind == "acquire":
                pending.append(release_for[compiled_task.resource])

        if main_error is not None:
            for compiled_task in reversed(pending):
                self._release(compiled_task, release_errors)

        if main_error is not None:
            if release_errors:
                combined = f"{main_error}\n\nCleanup errors:\n" + "\n".join(release_errors)
                raise RuntimeError(combined) from main_error
            raise main_error

        if release_errors:
            raise RuntimeError("Cleanup failed:\n" + "\n".join(release_errors))

    def _release(self, compiled_task: CompiledTask[object], release_errors: list[str]) -> None:
        """Run one release unit, honoring infrastructure retention, collecting errors."""
        resource = compiled_task.resource
        if self.keep_infrastructure and resource is not None and resource.infrastructure:
            return
        try:
            with task_lifecycle(task_id=compiled_task.task_id, title=compiled_task.task.title):
                compiled_task.task.run()
        except Exception as exc:
            release_errors.append(str(exc))

    def add(self, task: Task[Any], requires: tuple[Resource, ...] = ()) -> Workflow:
        """Record a task definition and the resources it consumes. Order preserved."""
        self._definitions.append((task, requires))
        return self

    def compile(self) -> CompiledWorkflow:
        """Assign stable, deterministic IDs to the recorded task definitions.

        Each `Resource` referenced across `requires` gets one acquire unit
        spliced immediately before its first consumer and one release unit
        immediately after its last consumer. Releases landing at the same point
        run in reverse acquisition order (last-acquired-first-released). IDs are
        `{ordinal:03d}.{slug}` derived from position in the final merged
        sequence; duplicate titles are disambiguated by ordinal.
        """
        if not self.workflow_id:
            raise ValueError("Workflow.compile() requires a non-empty workflow_id")

        merged = self._merge_resources()
        compiled_tasks = tuple(
            CompiledTask(
                task_id=f"{ordinal:03d}.{_slugify(entry.task.title)}",
                task=entry.task,
                required_resources=entry.required_resources,
                kind=entry.kind,
                resource=entry.resource,
            )
            for ordinal, entry in enumerate(merged, start=1)
        )
        return CompiledWorkflow(workflow_id=self.workflow_id, tasks=compiled_tasks)

    def _merge_resources(self) -> list[CompiledTask[Any]]:
        """Splice acquire/release units around consumers (IDs assigned by caller)."""
        # First/last consumer index per resource, in discovery order.
        first: dict[Resource, int] = {}
        last: dict[Resource, int] = {}
        for index, (_task, requires) in enumerate(self._definitions):
            for resource in requires:
                first.setdefault(resource, index)
                last[resource] = index

        # Acquisition order: by first-consumer index, then discovery order.
        acquire_rank = {
            resource: rank
            for rank, resource in enumerate(sorted(first, key=lambda r: first[r]))
        }
        acquires_before: dict[int, list[Resource]] = {}
        releases_after: dict[int, list[Resource]] = {}
        for resource in first:
            acquires_before.setdefault(first[resource], []).append(resource)
        for resource in last:
            releases_after.setdefault(last[resource], []).append(resource)
        # Same-point acquires keep acquisition order; same-point releases reverse it.
        for group in acquires_before.values():
            group.sort(key=lambda r: acquire_rank[r])
        for group in releases_after.values():
            group.sort(key=lambda r: acquire_rank[r], reverse=True)

        merged: list[CompiledTask[Any]] = []
        for index, (task, requires) in enumerate(self._definitions):
            for resource in acquires_before.get(index, ()):
                op = ResourceOp(title=resource.title, fn=resource.acquire)
                merged.append(CompiledTask(task_id="", task=op, kind="acquire", resource=resource))
            merged.append(
                CompiledTask(task_id="", task=task, required_resources=requires, kind="consumer")
            )
            for resource in releases_after.get(index, ()):
                op = ResourceOp(title=resource.release_title, fn=resource.release)
                merged.append(CompiledTask(task_id="", task=op, kind="release", resource=resource))
        return merged
