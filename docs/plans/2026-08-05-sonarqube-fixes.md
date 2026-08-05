# SonarQube Findings Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the 15 open SonarQube issues on `sonata-python` to 0 with zero behavioral change.

**Architecture:** Behavior-preserving fixes only. Three trivial edits (S1066, S3358, S5886), one narrowed except (S5754) matching an existing in-repo pattern, NOSONAR-with-justification on five intentional catch-all collectors (S5754), and extraction refactors on five over-complex functions (S3776) using the existing test suite as the regression net. Design: `docs/specs/2026-08-05-sonarqube-fixes-design.md`.

**Tech Stack:** Python >= 3.12, `uv`, pytest, ruff, basedpyright, sonar-scanner + ephemeral SonarQube (scripts/sonar.sh).

## Global Constraints

- Zero runtime dependencies; Python >= 3.12; line length 100; ruff (ANN, E, F, I).
- **No behavioral change** — no public API changes, no error-handling semantics changes. Every branch stays identical; refactors only move code.
- NOSONAR comments are allowed only with a justification comment naming the design intent (spec-approved).
- Every task ends green: relevant tests pass, `uv run ruff check src tests`, `uv run basedpyright`.
- Commit per task with the given message.

---
### Task 1: Extract `_maybe_resume_skip` from `_execute_recorded` (S3776, folds in S1066)

**Files:**
- Modify: `src/sonata_engine/core/workflow.py` (add helper before `_execute_recorded` at line 65; replace lines 85-89)
- Test: `tests/core/test_workflow.py`, `tests/test_resume.py`, `tests/core/test_step_scope.py`

**Interfaces:**
- Produces: module function `_maybe_resume_skip(*, task_id: str, task: Task[Any], jrnl: Journal | None, resume: bool) -> TaskExecution | None` — returns the `skipped` execution when resume says skip, `None` otherwise.

- [ ] **Step 1: Add the module-level helper and call it**

Insert before `def _execute_recorded(`:

```python
def _maybe_resume_skip(
    *, task_id: str, task: Task[Any], jrnl: Journal | None, resume: bool
) -> TaskExecution | None:
    """The 'skipped' execution when resume says skip; None otherwise."""
    if jrnl is None or not resume:
        return None
    if jrnl.decide_task(task_id, task) != "skip":
        return None
    jrnl.record_skipped(task_id, jrnl.next_attempt(task_id))
    _task_skipped(task_id=task_id, title=task.title)
    return TaskExecution(task_id=task_id, status="skipped", outcome=None)
```

In `_execute_recorded`, replace the resume block:

```python
    if jrnl is not None and resume:
        if jrnl.decide_task(task_id, task) == "skip":
            jrnl.record_skipped(task_id, jrnl.next_attempt(task_id))
            _task_skipped(task_id=task_id, title=task.title)
            return TaskExecution(task_id=task_id, status="skipped", outcome=None)
```

with:

```python
    skip = _maybe_resume_skip(task_id=task_id, task=task, jrnl=jrnl, resume=resume)
    if skip is not None:
        return skip
```

(The S1066 finding was the nested `if`s; the extraction removes the nesting entirely.)

- [ ] **Step 2: Regression — relevant tests**

Run: `uv run pytest tests/core/test_workflow.py tests/test_resume.py tests/core/test_step_scope.py`
Expected: all pass.

- [ ] **Step 3: Lint and type check**

Run: `uv run ruff check src tests && uv run basedpyright`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/sonata_engine/core/workflow.py
git commit -m "refactor: extract resume-skip check from _execute_recorded
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
### Task 2: Split `_run_compiled` into `_release_pending` + `_raise_final` (S3776)

**Files:**
- Modify: `src/sonata_engine/core/workflow.py` (methods on `Workflow`, after `_retained_resources`; replace the loop + error-combination tail of `_run_compiled`, lines ~226-288)
- Test: `tests/core/test_workflow.py`, `tests/core/test_resource_cleanup.py`, `tests/test_workflow_e2e.py`, `tests/test_resume.py`

**Interfaces:**
- Consumes: existing `self._release(...)` and `self._run_unit(...)` signatures.
- Produces: `_release_pending(self, pending, release_errors, state, jrnl, retained_resources) -> None`; `_raise_final(self, main_error: BaseException | None, release_errors: list[BaseException]) -> None` (raises, returns None otherwise).

- [ ] **Step 1: Add the two helper methods** (after `_retained_resources`):

```python
    def _release_pending(
        self,
        pending: list[CompiledTask[object]],
        release_errors: list[BaseException],
        state: _RunState,
        jrnl: Journal | None,
        retained_resources: frozenset[int],
    ) -> None:
        """Run every pending release in reverse acquisition order."""
        for compiled_task in reversed(pending):
            self._release(compiled_task, release_errors, state, jrnl, retained_resources)

    def _raise_final(
        self, main_error: BaseException | None, release_errors: list[BaseException]
    ) -> None:
        """Raise the run's outcome: critical errors first, then the primary
        error with cleanup notes, else the collected cleanup errors."""
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
```

- [ ] **Step 2: Rewrite the walk and tail of `_run_compiled`**

Replace:

```python
        for compiled_task in compiled.tasks:
            if compiled_task.kind == "release":
                execution = self._release(
                    compiled_task, release_errors, state, jrnl, retained_resources
                )
                if execution is not None:
                    executions.append(execution)
                pending = [p for p in pending if p is not compiled_task]
                continue
            try:
                on_executed = None
                if compiled_task.kind == "acquire":
                    resource = compiled_task.resource
                    if resource is None:
                        raise RuntimeError("acquire task has no resource")
                    release_task = release_for[id(resource)]
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
                self._release(compiled_task, release_errors, state, jrnl, retained_resources)

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
```

with:

```python
        try:
            for compiled_task in compiled.tasks:
                if compiled_task.kind == "release":
                    execution = self._release(
                        compiled_task, release_errors, state, jrnl, retained_resources
                    )
                    if execution is not None:
                        executions.append(execution)
                    pending = [p for p in pending if p is not compiled_task]
                    continue
                on_executed = None
                if compiled_task.kind == "acquire":
                    resource = compiled_task.resource
                    if resource is None:
                        raise RuntimeError("acquire task has no resource")
                    release_task = release_for[id(resource)]
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

        if main_error is not None:
            self._release_pending(pending, release_errors, state, jrnl, retained_resources)
        self._raise_final(main_error, release_errors)
        return WorkflowResult(workflow_id=compiled.workflow_id, tasks=tuple(executions))
```

Semantics preserved: the try now wraps the whole walk; `self._release` never raises (it collects), and the `break` on failure is equivalent to abandoning the loop via the `except`. The NOSONAR for this `except` is added in Task 7.

- [ ] **Step 3: Regression — relevant tests**

Run: `uv run pytest tests/core/test_workflow.py tests/core/test_resource_cleanup.py tests/test_workflow_e2e.py tests/test_resume.py`
Expected: all pass.

- [ ] **Step 4: Lint and type check**

Run: `uv run ruff check src tests && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/sonata_engine/core/workflow.py
git commit -m "refactor: split _run_compiled into release-pending and final-raise helpers
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
### Task 3: Extract `_retention_skip` from `_release` (S3776)

**Files:**
- Modify: `src/sonata_engine/core/workflow.py` (new method after `_release`; replace lines 389-419)
- Test: `tests/core/test_resource_cleanup.py`, `tests/test_retention.py`, `tests/core/test_workflow.py`

**Interfaces:**
- Consumes: `_record_release_outcome(self, release_errors, record: Callable[[], None]) -> None`.
- Produces: `_retention_skip(self, *, resource, compiled_task, release_errors, state, jrnl) -> TaskExecution | None` — returns the `skipped` execution when the retention was recorded; `None` when the record could not be written (caller then releases for real).

- [ ] **Step 1: Add the `_retention_skip` method** (after `_release`):

```python
    def _retention_skip(
        self,
        *,
        resource: Resource[Any],
        compiled_task: CompiledTask[object],
        release_errors: list[BaseException],
        state: _RunState,
        jrnl: Journal | None,
    ) -> TaskExecution | None:
        """Record the retention and report the skipped release; None when the
        record could not be written (the caller then releases for real).

        Retention is a promise that a later run can finish the job, and that
        run will have no `_RunState`. If the value cannot be written down the
        promise cannot be kept, so the caller falls through and releases now:
        a resource held with no way to release it is worse than one released
        early.
        """
        if jrnl is None or not jrnl.record_retained(
            resource.title, self._retention_order, state.values.get(id(resource))
        ):
            return None
        self._retention_order += 1
        if jrnl is not None:
            self._record_release_outcome(
                release_errors,
                lambda: jrnl.record_skipped(
                    compiled_task.task_id,
                    jrnl.next_attempt(compiled_task.task_id),
                ),
            )
        try:
            _task_skipped(task_id=compiled_task.task_id, title=compiled_task.task.title)
        except BaseException as exc:
            release_errors.append(exc)
        state.remove(resource)
        return TaskExecution(task_id=compiled_task.task_id, status="skipped", outcome=None)
```

- [ ] **Step 2: Rewrite the head of `_release`**

Replace (inside `_release`, the retention branch):

```python
        resource = compiled_task.resource
        if resource is not None and id(resource) in retained_resources:
            # Retention is a promise that a later run can finish the job, and that
            # run will have no `_RunState`. If the value cannot be written down the
            # promise cannot be kept, so fall through and release now: a resource
            # held with no way to release it is worse than one released early.
            if jrnl is not None and not jrnl.record_retained(
                resource.title,
                self._retention_order,
                state.values.get(id(resource)),
            ):
                retained_resources = frozenset()
        if resource is not None and id(resource) in retained_resources:
            self._retention_order += 1
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
```

with:

```python
        resource = compiled_task.resource
        if resource is not None and id(resource) in retained_resources:
            skipped = self._retention_skip(
                resource=resource,
                compiled_task=compiled_task,
                release_errors=release_errors,
                state=state,
                jrnl=jrnl,
            )
            if skipped is not None:
                return skipped
```

Semantics preserved: when `record_retained` fails the helper returns `None` and the code falls through to the release path — the original reached the same point via `retained_resources = frozenset()`.

- [ ] **Step 3: Regression — relevant tests**

Run: `uv run pytest tests/core/test_resource_cleanup.py tests/test_retention.py tests/core/test_workflow.py`
Expected: all pass.

- [ ] **Step 4: Lint and type check**

Run: `uv run ruff check src tests && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/sonata_engine/core/workflow.py
git commit -m "refactor: extract retention-skip branch from _release
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
### Task 4: Extract the dependency DFS from `_merge_resources` (S3776)

The DFS must move to **module level** — Sonar counts nested-function bodies in the enclosing function's complexity, so a nested closure would not lower the score.

**Files:**
- Modify: `src/sonata_engine/core/workflow.py` (module-level `_MergeState` dataclass + `_register_dependency` before `Workflow`; rewrite `_merge_resources` body, lines ~508-609)
- Test: `tests/core/test_compile_selection.py`, `tests/core/test_compiled.py`, `tests/core/test_workflow.py`

**Interfaces:**
- Produces: `_MergeState` (dataclass with fields `first`, `last`, `resources`, `discovery`, `colors`, `stack`, `seen_at_index` — all `dict[int, ...]`/`list`); `_register_dependency(state: _MergeState, resource: Resource[Any], index: int) -> None` (raises `ResourceDependencyCycleError`).

- [ ] **Step 1: Add `_MergeState` and `_register_dependency`** (module level, before `class Workflow`):

```python
@dataclass
class _MergeState:
    """DFS state for `_merge_resources`, threaded through the recursion."""

    # Keys deliberately use object identity: resource callbacks and a malformed
    # cyclic graph must never be hashed while the compiler is diagnosing it.
    first: dict[int, int] = field(default_factory=dict)
    last: dict[int, int] = field(default_factory=dict)
    resources: dict[int, Resource[Any]] = field(default_factory=dict)
    discovery: list[int] = field(default_factory=list)
    colors: dict[int, str] = field(default_factory=dict)
    stack: list[Resource[Any]] = field(default_factory=list)
    seen_at_index: dict[int, int] = field(default_factory=dict)


def _register_dependency(state: _MergeState, resource: Resource[Any], index: int) -> None:
    """Register one resource at one consumer index, walking its dependencies.

    Depth-first visit; cycles raise `ResourceDependencyCycleError`. A 'done'
    node is re-walked only when re-encountered at a later index -- without
    this, a diamond-shaped graph re-walks whole shared subtrees on every path
    to them, doubling work per level -- exponential in depth.
    """
    key = id(resource)
    color = state.colors.get(key, "unseen")
    if color == "visiting":
        cycle_start = next(i for i, item in enumerate(state.stack) if item is resource)
        cycle = (*state.stack[cycle_start:], resource)
        raise ResourceDependencyCycleError(
            "resource dependency cycle: " + " -> ".join(item.title for item in cycle)
        )
    if color == "unseen":
        state.colors[key] = "visiting"
        state.stack.append(resource)
        for dependency in resource.requires:
            _register_dependency(state, dependency, index)
        state.stack.pop()
        state.colors[key] = "done"
        state.resources[key] = resource
        state.discovery.append(key)
        state.seen_at_index[key] = index
    elif state.seen_at_index.get(key) != index:
        state.seen_at_index[key] = index
        for dependency in resource.requires:
            _register_dependency(state, dependency, index)
    state.first.setdefault(key, index)
    state.last[key] = index
```

- [ ] **Step 2: Rewrite `_merge_resources`**

Replace the entire body from the `# First/last consumer index per resource, in DFS declaration order.` comment through `return merged` with:

```python
        # First/last consumer index per resource, in DFS declaration order.
        state = _MergeState()
        for index, (_task, requires) in enumerate(definitions):
            for resource in requires:
                _register_dependency(state, resource, index)

        # Acquisition order: by first-consumer index, then discovery order.
        acquire_rank = {key: rank for rank, key in enumerate(state.discovery)}
        acquires_before: dict[int, list[int]] = {}
        releases_after: dict[int, list[int]] = {}
        for key in state.discovery:
            acquires_before.setdefault(state.first[key], []).append(key)
            releases_after.setdefault(state.last[key], []).append(key)
        # Same-point acquires keep acquisition order; same-point releases reverse it.
        for group in acquires_before.values():
            group.sort(key=lambda key: acquire_rank[key])
        for group in releases_after.values():
            group.sort(key=lambda key: acquire_rank[key], reverse=True)

        merged: list[CompiledTask[Any]] = []
        for index, (task, requires) in enumerate(definitions):
            for key in acquires_before.get(index, ()):
                resource = state.resources[key]
                op = ResourceOp(
                    title=resource.title,
                    resource=resource,
                    operation=ResourceOperation.ACQUIRE,
                    idempotent=resource.acquire_idempotent,
                )
                merged.append(
                    CompiledTask(
                        task_id="",
                        task=op,
                        required_resources=resource.requires,
                        kind="acquire",
                        resource=resource,
                    )
                )
            merged.append(
                CompiledTask(task_id="", task=task, required_resources=requires, kind="consumer")
            )
            for key in releases_after.get(index, ()):
                resource = state.resources[key]
                op = ResourceOp(
                    title=resource.release_title,
                    resource=resource,
                    operation=ResourceOperation.RELEASE,
                )
                merged.append(
                    CompiledTask(
                        task_id="",
                        task=op,
                        required_resources=resource.requires,
                        kind="release",
                        resource=resource,
                    )
                )
        return merged
```

`dataclass` and `field` are already imported in this module.

- [ ] **Step 3: Regression — relevant tests**

Run: `uv run pytest tests/core/test_compile_selection.py tests/core/test_compiled.py tests/core/test_workflow.py`
Expected: all pass (cycle detection is covered by `test_workflow.py`).

- [ ] **Step 4: Lint and type check**

Run: `uv run ruff check src tests && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/sonata_engine/core/workflow.py
git commit -m "refactor: move resource-graph DFS to a module-level register helper
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
### Task 5: Split `Journal._load` into `_parse_record` + `_fold_record` (S3776)

**Files:**
- Modify: `src/sonata_engine/journal.py` (two new methods after `_load`; rewrite `_load` body, lines ~224-313)
- Test: `tests/test_journal.py`, `tests/test_resume.py`, `tests/test_retention.py`

**Interfaces:**
- Consumes: existing `_truncate_torn_tail(self, offset: int)`, `_evidence_from_json(raw)` module function, `TaskState`.
- Produces: `_parse_record(self, raw_line: bytes, index: int, is_torn_tail: bool, line_start: int) -> dict[str, Any] | None` — decoded+parsed record, or `None` for blank lines and truncated torn tails; `_fold_record(self, record: dict[str, Any], index: int, states: dict[str, TaskState]) -> None` — merges one task-outcome record, raises `CorruptJournalError`.

- [ ] **Step 1: Rewrite `_load`** (replace the loop body between the `for index, raw_line in enumerate(lines):` line and `return states`):

```python
        for index, raw_line in enumerate(lines):
            line_start = offset
            offset += len(raw_line)
            is_torn_tail = index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r"))
            record = self._parse_record(raw_line, index, is_torn_tail, line_start)
            if record is None:
                continue
            version = record.get("schema_version")
            if version != SCHEMA_VERSION:
                raise UnsupportedJournalSchemaError(
                    f"{self.path}: schema_version {version!r}, expected {SCHEMA_VERSION}"
                )
            if record.get("workflow_id") != self.workflow_id:
                continue
            fingerprint = record.get("workflow_fingerprint")
            if fingerprint != self.workflow_fingerprint:
                if self._resume:
                    raise WorkflowTopologyMismatchError(
                        f"{self.path}: workflow {self.workflow_id!r} has fingerprint "
                        f"{fingerprint!r}, expected {self.workflow_fingerprint!r}"
                    )
                if not warned_mismatch:
                    warned_mismatch = True
                    warnings.warn(
                        f"{self.path}: existing journal records for workflow "
                        f"{self.workflow_id!r} have fingerprint {fingerprint!r}, expected "
                        f"{self.workflow_fingerprint!r}; ignoring them and appending a new "
                        "topology to the same file (a Sonata upgrade invalidates prior "
                        "journals -- see README.md)",
                        stacklevel=2,
                    )
                continue
            if record.get("kind") == "retained":
                # Resource retention, not a task outcome: no task_id, no part in
                # resume decisions. `release_retained` is what reads these back.
                self._retained.append(record)
                continue
            self._fold_record(record, index, states)
        return states
```

- [ ] **Step 2: Add `_parse_record` and `_fold_record`** (after `_load`):

```python
    def _parse_record(
        self, raw_line: bytes, index: int, is_torn_tail: bool, line_start: int
    ) -> dict[str, Any] | None:
        """Decode and parse one journal line; None for a blank line or torn tail.

        A torn tail (crash mid-write) is truncated and dropped. Any other
        malformed line raises `CorruptJournalError`.
        """
        try:
            line = raw_line.decode("utf-8")
        except UnicodeError as exc:
            if is_torn_tail:
                self._truncate_torn_tail(line_start)
                return None
            raise CorruptJournalError(
                f"{self.path}:{index + 1}: record is not valid UTF-8"
            ) from exc
        if not line.strip():
            return None
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if is_torn_tail:
                self._truncate_torn_tail(line_start)
                return None
            raise CorruptJournalError(
                f"{self.path}:{index + 1}: malformed JSON record"
            ) from exc
        if not isinstance(record, dict):
            raise CorruptJournalError(
                f"{self.path}:{index + 1}: journal record must be an object"
            )
        return record

    def _fold_record(
        self, record: dict[str, Any], index: int, states: dict[str, TaskState]
    ) -> None:
        """Merge one task-outcome record into `states`; raises on malformed data.

        Records are appended in order: a later record for the same (or higher)
        attempt supersedes an earlier one -- terminal replaces started.
        """
        try:
            task_id = str(record["task_id"])
            attempt = int(record["attempt"])
            status = str(record["status"])
            raw_evidence = record.get("evidence", [])
            evidence = _evidence_from_json(raw_evidence)
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptJournalError(
                f"{self.path}:{index + 1}: malformed journal record"
            ) from exc
        if status not in {"pending", "started", "passed", "failed", "skipped"}:
            raise CorruptJournalError(
                f"{self.path}:{index + 1}: invalid task status {status!r}"
            )
        existing = states.get(task_id)
        if existing is None or attempt >= existing.attempt:
            states[task_id] = TaskState(
                task_id=task_id,
                attempt=attempt,
                status=status,
                evidence=evidence,
            )
```

Semantics preserved: a torn tail is always the last line, so the original `break` after truncation and the new `continue` (via `None`) are equivalent. `warned_mismatch` stays in `_load`; the retained-record branch keeps the original comment.

- [ ] **Step 3: Regression — relevant tests**

Run: `uv run pytest tests/test_journal.py tests/test_resume.py tests/test_retention.py`
Expected: all pass (torn-tail, corruption, and schema-mismatch paths are covered).

- [ ] **Step 4: Lint and type check**

Run: `uv run ruff check src tests && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/sonata_engine/journal.py
git commit -m "refactor: split Journal._load into parse and fold helpers
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
### Task 6: Trivial fixes — S5886 casts and S3358 (behavior-identical)

**Files:**
- Modify: `src/sonata_engine/core/workflow.py` (import line 6; `make_inputs` at lines 134-139 and 332-346)
- Modify: `src/sonata_engine/workflow/event_builders.py:16-19`
- Test: `tests/core/test_step_scope.py`, `tests/core/test_workflow.py`, `tests/workflow/test_event_builders.py`

**Interfaces:**
- None (no signature changes).

- [ ] **Step 1: `cast` in `workflow.py`**

Change the import:

```python
from typing import Any
```

to:

```python
from typing import Any, cast
```

Replace the `make_inputs` in `_StepScope.run_step`:

```python
        def make_inputs() -> TaskInputs:
            return replace(
                self.base_inputs,
                _upstream=upstream,
                _step_scope=replace(self, prefix=step_id),
            )
```

with:

```python
        def make_inputs() -> TaskInputs:
            return cast(
                TaskInputs,
                replace(
                    self.base_inputs,
                    _upstream=upstream,
                    _step_scope=replace(self, prefix=step_id),
                ),
            )
```

Replace the `make_inputs` in `_run_unit`:

```python
        def make_inputs() -> TaskInputs:
            base = state.inputs_for(
                compiled_task.resource.requires
                if compiled_task.kind == "acquire" and compiled_task.resource is not None
                else compiled_task.required_resources
            )
            return replace(
                base,
                _step_scope=_StepScope(
                    prefix=compiled_task.task_id,
                    base_inputs=base,
                    jrnl=jrnl,
                    resume=resume,
                ),
            )
```

with:

```python
        def make_inputs() -> TaskInputs:
            base = state.inputs_for(
                compiled_task.resource.requires
                if compiled_task.kind == "acquire" and compiled_task.resource is not None
                else compiled_task.required_resources
            )
            return cast(
                TaskInputs,
                replace(
                    base,
                    _step_scope=_StepScope(
                        prefix=compiled_task.task_id,
                        base_inputs=base,
                        jrnl=jrnl,
                        resume=resume,
                    ),
                ),
            )
```

(`dataclasses.replace` is typed to return `DataclassInstance`; `cast` restores the declared `TaskInputs`.)

- [ ] **Step 2: S3358 in `event_builders.py`**

Replace:

```python
    resolved_task_id = (
        task_id if task_id is not None else (active.task_id if inherit_task_id else None)
    )
```

with:

```python
    if task_id is not None:
        resolved_task_id = task_id
    elif inherit_task_id:
        resolved_task_id = active.task_id
    else:
        resolved_task_id = None
```

- [ ] **Step 3: Regression — relevant tests**

Run: `uv run pytest tests/core/test_step_scope.py tests/core/test_workflow.py tests/workflow/test_event_builders.py`
Expected: all pass.

- [ ] **Step 4: Lint and type check**

Run: `uv run ruff check src tests && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/sonata_engine/core/workflow.py src/sonata_engine/workflow/event_builders.py
git commit -m "fix: satisfy S5886 replace() typing and S3358 nested ternary
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
### Task 7: S5754 — narrow the journal catch; justify the five intentional catch-alls

**Files:**
- Modify: `src/sonata_engine/core/workflow.py` (line ~112; `except` in `_run_compiled`; `except` in `_retention_skip`; `except` in `_release`)
- Modify: `src/sonata_engine/retention.py:100`
- Modify: `src/sonata_engine/workflow/reporting.py:91`
- Test: `tests/core/test_resource_cleanup.py`, `tests/test_retention.py`, `tests/workflow/test_reporting.py`

**Interfaces:**
- None.

- [ ] **Step 1: Narrow the journal catch in `_execute_recorded`**

Replace:

```python
            except BaseException as journal_error:
                exc.add_note(f"Failed to record task failure: {journal_error}")
```

with:

```python
            except OSError as journal_error:
                exc.add_note(f"Failed to record task failure: {journal_error}")
```

(Journal writes are file I/O; this matches the existing `_record_release_outcome` pattern that already catches only `OSError`.)

- [ ] **Step 2: NOSONAR on the five intentional catch-alls**

`workflow.py`, in `_run_compiled` (the walk catch from Task 2):

```python
        except BaseException as exc:  # NOSONAR S5754 - stop the walk so pending releases can run; re-raised by _raise_final
            main_error = exc
```

`workflow.py`, in `_retention_skip` (from Task 3):

```python
        except BaseException as exc:  # NOSONAR S5754 - a reporting failure must not abort the retention skip
            release_errors.append(exc)
```

`workflow.py`, in `_release` (the release-body catch):

```python
        except BaseException as exc:  # NOSONAR S5754 - collect the failure; every pending release must still run
            release_errors.append(exc)
```

`retention.py:100`:

```python
        except BaseException as error:  # noqa: BLE001 - reported together below (NOSONAR S5754: keep releasing)
            errors.append(error)
```

`reporting.py:91`:

```python
            except BaseException as reporting_error:  # NOSONAR S5754 - sink failure is noted on the original exc, which is re-raised
                exc.add_note(f"Failed to emit task.failed for {task_id}: {reporting_error}")
```

- [ ] **Step 3: Regression — relevant tests**

Run: `uv run pytest tests/core/test_resource_cleanup.py tests/test_retention.py tests/workflow/test_reporting.py`
Expected: all pass.

- [ ] **Step 4: Lint and type check**

Run: `uv run ruff check src tests && uv run basedpyright`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/sonata_engine/core/workflow.py src/sonata_engine/retention.py src/sonata_engine/workflow/reporting.py
git commit -m "fix: narrow journal catch to OSError; justify intentional catch-alls
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
### Task 8: Verify — full suite and SonarQube re-run

**Files:**
- None (verification only).

- [ ] **Step 1: Full test suite, lint, type check**

Run: `uv run pytest && uv run ruff check . && uv run basedpyright`
Expected: all pass, clean.

- [ ] **Step 2: Re-run SonarQube**

Run: `scripts/sonar.sh`
Expected: the report prints `python: 0 open issues`. If any issue remains, check the rule and line: a misplaced NOSONAR (wrong line) is the likely cause — fix the comment placement and re-run.

- [ ] **Step 3: Confirm the server is healthy and report**

Expected output ends with `Server left running at http://127.0.0.1:9000`.

- [ ] **Step 4: Commit any stragglers** (docs only — the spec was committed already)

```bash
git add docs/plans/2026-08-05-sonarqube-fixes.md
git commit -m "docs: implementation plan for SonarQube findings fixes
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---
## Self-review notes

- Spec coverage: all 15 issues mapped — S1066 (Task 1), S3776 ×5 (Tasks 1-5), S5886 ×2 (Task 6), S3358 (Task 6), S5754 ×6 (Task 7: 1 narrow + 5 NOSONAR). Verification (Task 8).
- NOSONAR fallback ("if a helper still exceeds 15") is not needed in this plan: every extracted helper is at or below ~13 by construction.
- Type consistency: `_retention_skip`/`_maybe_resume_skip` return `TaskExecution | None`; `_parse_record` returns `dict[str, Any] | None`; `_fold_record` returns `None` and mutates `states` — matching the call sites in each task.
