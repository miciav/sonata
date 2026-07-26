from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from sonata_engine.core.compiled import (
    CompiledTask,
    CompiledWorkflow,
    TaskExecution,
    WorkflowResult,
)
from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.resource_task import Resource, ResourceOp, ResourceOperation
from sonata_engine.core.selection import Selection
from sonata_engine.core.task import ReusableTask, Task
from sonata_engine.errors import (
    InvalidTaskOutcomeError,
    ResumeConfigurationError,
    SelectionError,
)
from sonata_engine.journal import Journal, JournalConfig, Verifier
from sonata_engine.workflow.reporting import _task_lifecycle, _task_skipped

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


@dataclass
class _RunState:
    """Runner-private mutable resource values for one workflow execution."""

    values: dict[Resource[Any], object] = field(default_factory=dict)
    _missing: object = field(default_factory=object, init=False, repr=False)

    def inputs_for(self, accessible: tuple[Resource[Any], ...]) -> TaskInputs:
        return TaskInputs._for_resources(self.values, frozenset(accessible))

    def publish(self, resource: Resource[Any], value: object) -> None:
        self.values[resource] = value

    def remove(self, resource: Resource[Any]) -> object:
        return self.values.pop(resource, self._missing)


def _slugify(title: str) -> str:
    return _SLUG_INVALID_CHARS.sub("-", title.lower()).strip("-")


def _resolve_slug(slugs: list[str], wanted: str) -> int:
    """Index of the single consumer whose slug is `wanted`.

    Ambiguity is an error rather than an implicit multi-select: duplicate titles
    are legal (the ordinal disambiguates their IDs) but stop being addressable.
    """
    matches = [index for index, slug in enumerate(slugs) if slug == wanted]
    if not matches:
        available = ", ".join(sorted(set(slugs)))
        raise SelectionError(f"no task matches slug {wanted!r}; available: {available}")
    if len(matches) > 1:
        raise SelectionError(
            f"slug {wanted!r} matches {len(matches)} tasks; "
            "titles must be unique for a task to be selectable"
        )
    return matches[0]


@dataclass
class Workflow:
    """Ordered workflow builder, compiler, and executor."""

    workflow_id: str
    keep_infrastructure: bool = False
    _definitions: list[tuple[Task[Any], tuple[Resource, ...]]] = field(
        default_factory=list, init=False, repr=False
    )

    def run(
        self,
        *,
        journal: JournalConfig | None = None,
        resume: bool = False,
        verifiers: Mapping[str, Verifier] | None = None,
        select: Selection | None = None,
    ) -> WorkflowResult:
        """Compile and run this workflow using compiler-owned task identities.

        `select` narrows the run to a slice of consumer tasks; their resources
        are still acquired and released around them. A sliced run has a
        different fingerprint, so `resume` across one fails closed.
        """
        return self._run_compiled(
            self.compile(select=select),
            journal=journal,
            resume=resume,
            verifiers=verifiers,
        )

    def _run_compiled(
        self,
        compiled: CompiledWorkflow,
        *,
        journal: JournalConfig | None = None,
        resume: bool = False,
        verifiers: Mapping[str, Verifier] | None = None,
    ) -> WorkflowResult:
        """Run compiled tasks in order, owning their lifecycle events.

        With no `journal` and `resume=False` this behaves exactly as before -- the
        journal is optional. When a `journal` is configured the runner records every
        `started`/`passed`/`failed`/`skipped` outcome (tasks never touch the journal
        themselves). When `resume=True` it consults the recorded state to skip verified
        reusable tasks and retry safely; `resume=True` without a `journal` is rejected.

        Happy path: acquire/consumer/release units execute in the linear order
        `compile()` placed them -- releases already sit after their last consumer.

        Failure path: when a consumer or acquire raises, we stop the main walk and run
        the release unit for every resource acquired-but-not-yet-released, in reverse
        acquisition order. Release errors never abort remaining releases; they are
        collected and combined with the primary error. `keep_infrastructure` skips a
        release only when its `Resource` is `infrastructure`.
        """
        if resume and journal is None:
            raise ResumeConfigurationError("resume=True requires a JournalConfig")
        jrnl = (
            Journal(journal, compiled, verifiers, resume=resume) if journal is not None else None
        )

        release_for = {ct.resource: ct for ct in compiled.tasks if ct.kind == "release"}
        # Release units for acquired-but-not-yet-released resources, in acquisition order.
        pending: list[CompiledTask[object]] = []
        release_errors: list[BaseException] = []
        main_error: BaseException | None = None
        executions: list[TaskExecution] = []
        state = _RunState()

        for compiled_task in compiled.tasks:
            if compiled_task.kind == "release":
                execution = self._release(compiled_task, release_errors, state, jrnl)
                if execution is not None:
                    executions.append(execution)
                pending = [p for p in pending if p is not compiled_task]
                continue
            try:
                on_executed = None
                if compiled_task.kind == "acquire":
                    release_task = release_for[compiled_task.resource]
                    on_executed = partial(pending.append, release_task)
                executions.append(
                    self._run_unit(
                        compiled_task,
                        jrnl,
                        state,
                        resume=resume,
                        on_executed=on_executed,
                    )
                )
            except BaseException as exc:
                main_error = exc
                break

        if main_error is not None:
            for compiled_task in reversed(pending):
                self._release(compiled_task, release_errors, state, jrnl)

        critical_error = (
            main_error
            if main_error is not None and not isinstance(main_error, Exception)
            else next(
                (error for error in release_errors if not isinstance(error, Exception)),
                None,
            )
        )
        if critical_error is not None:
            if main_error is not None and main_error is not critical_error:
                critical_error.add_note(f"Task error: {main_error}")
            for error in release_errors:
                if error is not critical_error:
                    critical_error.add_note(f"Cleanup error: {error}")
            raise critical_error

        if main_error is not None:
            if release_errors:
                combined = f"{main_error}\n\nCleanup errors:\n" + "\n".join(
                    str(error) for error in release_errors
                )
                raise RuntimeError(combined) from main_error
            raise main_error

        if release_errors:
            raise RuntimeError(
                "Cleanup failed:\n" + "\n".join(str(error) for error in release_errors)
            )
        return WorkflowResult(workflow_id=compiled.workflow_id, tasks=tuple(executions))

    def _next_attempt(self, jrnl: Journal | None, task_id: str) -> int:
        return jrnl.next_attempt(task_id) if jrnl is not None else 0

    def _run_unit(
        self,
        compiled_task: CompiledTask[object],
        jrnl: Journal | None,
        state: _RunState,
        *,
        resume: bool,
        on_executed: Callable[[], None] | None = None,
    ) -> TaskExecution:
        """Run one consumer/acquire unit, recording its journal outcome. Raises on failure.

        Consumers and acquire units consult the same resume decision matrix.
        `Resource.acquire_idempotent` controls whether an interrupted or failed
        acquire can retry; a passed acquire always reruns because it is non-reusable.
        Releases are handled separately by `_release`.

        A journal-write failure here is allowed to propagate (treated like any other
        task failure) -- unlike `_release`, which must keep attempting every pending
        release even if the journal itself is failing.
        """
        task_id = compiled_task.task_id
        task = compiled_task.task
        if jrnl is not None and resume:
            if jrnl.decide(compiled_task) == "skip":
                jrnl.record_skipped(task_id, jrnl.next_attempt(task_id))
                _task_skipped(task_id=task_id, title=task.title)
                return TaskExecution(task_id=task_id, status="skipped", outcome=None)

        attempt = self._next_attempt(jrnl, task_id)
        if jrnl is not None:
            # Flush the started record BEFORE executing, so a crash mid-task is durable.
            jrnl.record_started(task_id, attempt)
        try:
            with _task_lifecycle(task_id=task_id, title=task.title):
                inputs = state.inputs_for(
                    compiled_task.resource.requires
                    if compiled_task.kind == "acquire" and compiled_task.resource is not None
                    else compiled_task.required_resources
                )
                outcome = task.run(inputs)
                if not isinstance(outcome, TaskOutcome):
                    raise InvalidTaskOutcomeError(
                        f"{task_id} returned {outcome!r}, expected TaskOutcome"
                    )
                if isinstance(task, ReusableTask) and outcome.value is not None:
                    raise InvalidTaskOutcomeError(
                        f"{task_id} is reusable and returned a runtime value"
                    )
                if compiled_task.kind == "acquire" and compiled_task.resource is not None:
                    state.publish(compiled_task.resource, outcome.value)
                if on_executed is not None:
                    on_executed()
        except BaseException as exc:
            if jrnl is not None:
                try:
                    jrnl.record_failed(task_id, attempt)
                except BaseException as journal_error:
                    exc.add_note(f"Failed to record task failure: {journal_error}")
            raise
        if jrnl is not None:
            jrnl.record_passed(task_id, attempt, outcome.evidence)
        return TaskExecution(task_id=task_id, status="passed", outcome=outcome)

    def _record_release_outcome(
        self, release_errors: list[BaseException], record: Callable[[], None]
    ) -> None:
        """Run a `jrnl.record_*` call, collecting a journal I/O failure instead of
        letting it propagate. Cleanup must keep attempting every pending release even
        if the journal itself is failing (e.g. disk full) -- a journal-write error here
        must never mask the real release failure or abort the reverse-release loop."""
        try:
            record()
        except OSError as exc:
            exc.add_note("while recording release outcome")
            release_errors.append(exc)

    def _release(
        self,
        compiled_task: CompiledTask[object],
        release_errors: list[BaseException],
        state: _RunState,
        jrnl: Journal | None = None,
    ) -> TaskExecution | None:
        """Run one release unit, honoring infrastructure retention, collecting errors.

        A release is a non-reusable finalizer: it always runs, never consults prior
        state, but its outcome is still recorded when a journal is configured.
        """
        resource = compiled_task.resource
        if self.keep_infrastructure and resource is not None and resource.infrastructure:
            if jrnl is not None:
                self._record_release_outcome(
                    release_errors,
                    lambda: jrnl.record_skipped(
                        compiled_task.task_id,
                        jrnl.next_attempt(compiled_task.task_id),
                    ),
                )
            try:
                _task_skipped(
                    task_id=compiled_task.task_id,
                    title=compiled_task.task.title,
                )
            except BaseException as exc:
                release_errors.append(exc)
            state.remove(resource)
            return TaskExecution(task_id=compiled_task.task_id, status="skipped", outcome=None)
        task_id = compiled_task.task_id
        attempt = self._next_attempt(jrnl, task_id)
        if jrnl is not None:
            self._record_release_outcome(
                release_errors, lambda: jrnl.record_started(task_id, attempt)
            )
        try:
            with _task_lifecycle(task_id=task_id, title=compiled_task.task.title):
                if resource is None:
                    raise RuntimeError("release task has no resource")
                outcome = compiled_task.task.run(
                    state.inputs_for((resource, *resource.requires))
                )
                if not isinstance(outcome, TaskOutcome):
                    raise InvalidTaskOutcomeError(
                        f"{task_id} returned {outcome!r}, expected TaskOutcome"
                    )
        except BaseException as exc:
            release_errors.append(exc)
            if jrnl is not None:
                self._record_release_outcome(
                    release_errors, lambda: jrnl.record_failed(task_id, attempt)
                )
        else:
            if jrnl is not None:
                self._record_release_outcome(
                    release_errors, lambda: jrnl.record_passed(task_id, attempt)
                )
            return TaskExecution(task_id=task_id, status="passed", outcome=outcome)
        finally:
            if resource is not None:
                state.remove(resource)
        return None

    def add(self, task: Task[Any], requires: tuple[Resource, ...] = ()) -> Workflow:
        """Record a task definition and the resources it consumes. Order preserved."""
        self._definitions.append((task, requires))
        return self

    def compile(self, *, select: Selection | None = None) -> CompiledWorkflow:
        """Assign stable, deterministic IDs to the recorded task definitions.

        Each `Resource` referenced across `requires` gets one acquire unit
        spliced immediately before its first consumer and one release unit
        immediately after its last consumer. Releases landing at the same point
        run in reverse acquisition order (last-acquired-first-released). IDs are
        `{ordinal:03d}.{slug}` derived from position in the final merged
        sequence; duplicate titles are disambiguated by ordinal.

        `select` filters consumer definitions BEFORE resources are spliced, so
        the surviving consumers still get their acquire/release units. Ordinals
        renumber over the survivors: a sliced run is a different topology, and
        the journal fingerprint will refuse to resume across it.
        """
        if not self.workflow_id:
            raise ValueError("Workflow.compile() requires a non-empty workflow_id")

        merged = self._merge_resources(self._select(select))
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

    def _select(
        self, select: Selection | None
    ) -> list[tuple[Task[Any], tuple[Resource, ...]]]:
        """Filter consumer definitions by title slug, leaving resources to the compiler."""
        slugs = [_slugify(task.title) for task, _requires in self._definitions]
        for slug, (task, _requires) in zip(slugs, self._definitions):
            if not slug:
                raise ValueError(f"task title {task.title!r} produces an empty slug")
        if select is None or select.is_empty:
            return list(self._definitions)
        if select.only is not None:
            return [self._definitions[_resolve_slug(slugs, select.only)]]
        first = _resolve_slug(slugs, select.start) if select.start is not None else 0
        last = (
            _resolve_slug(slugs, select.until)
            if select.until is not None
            else len(self._definitions) - 1
        )
        if first > last:
            raise SelectionError(
                f"start {select.start!r} comes after until {select.until!r}"
            )
        return list(self._definitions[first : last + 1])

    def _merge_resources(
        self, definitions: list[tuple[Task[Any], tuple[Resource, ...]]]
    ) -> list[CompiledTask[Any]]:
        """Splice acquire/release units around consumers (IDs assigned by caller)."""
        # First/last consumer index per resource, in discovery order.
        first: dict[Resource, int] = {}
        last: dict[Resource, int] = {}
        def register(resource: Resource[Any], index: int) -> None:
            for dependency in resource.requires:
                register(dependency, index)
            first.setdefault(resource, index)
            last[resource] = index

        for index, (_task, requires) in enumerate(definitions):
            for resource in requires:
                register(resource, index)

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
        for index, (task, requires) in enumerate(definitions):
            for resource in acquires_before.get(index, ()):
                op = ResourceOp(
                    title=resource.title,
                    resource=resource,
                    operation=ResourceOperation.ACQUIRE,
                    idempotent=resource.acquire_idempotent,
                )
                merged.append(CompiledTask(task_id="", task=op, kind="acquire", resource=resource))
            merged.append(
                CompiledTask(task_id="", task=task, required_resources=requires, kind="consumer")
            )
            for resource in releases_after.get(index, ()):
                op = ResourceOp(
                    title=resource.release_title,
                    resource=resource,
                    operation=ResourceOperation.RELEASE,
                )
                merged.append(CompiledTask(task_id="", task=op, kind="release", resource=resource))
        return merged
