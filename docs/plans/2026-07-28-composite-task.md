# Composite Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a task be assembled from steps instead of hand-written, with each step's output feeding the next and each step journalled on its own, so a resumed composite skips the work it already finished.

**Architecture:** `Steps` is an ordinary `Task` the compiler does not special-case. The runner hands it a private step scope through `TaskInputs`; that scope owns everything needing the compiled unit id or the journal — child ids, resume decisions, records, lifecycle events — while `Steps` owns only the loop and the upstream value. One shared recorded-execution routine serves both compiled units and steps, so validation and journal error behaviour are inherited rather than copied.

**Tech Stack:** Python 3.12+, standard library only, pytest, Ruff, Basedpyright.

**Spec:** `docs/specs/2026-07-28-composite-task-design.md`

## Global Constraints

- Sonata takes no runtime dependencies and never imports from a downstream product. `tests/test_package_boundaries.py` enforces this.
- The compiler must not special-case `Steps`. It sees one compiled unit; `Selection` and ordinals are unaffected.
- No new event kinds. The four that exist are `task.started`, `task.passed`, `task.failed`, `task.skipped`.
- `subtask` stays the reporting-only primitive for hand-written tasks. **`Steps` does not use it** — it goes through the shared execution routine.
- `Steps.idempotent` is always `True`. Without it the enclosing unit's failed record raises `AmbiguousTaskStateError` before `Steps.run()` can consult any step record.
- A step declares no resources of its own; it observes what the composite declared.
- Coverage gate and lint settings in `pyproject.toml` apply as they stand; do not relax them.
- Gates: `uv run pytest`, `uv run ruff check .`, `uv run basedpyright`. All three green before every commit. Baseline on this branch is 178 passing.

---

### Task 1: `upstream()` on `TaskInputs`

**Files:**
- Modify: `src/sonata_engine/errors.py`
- Modify: `src/sonata_engine/core/inputs.py`
- Modify: `src/sonata_engine/__init__.py`
- Create: `tests/core/test_inputs.py`

**Interfaces:**
- Produces: `NoUpstreamValueError`, exported from `sonata_engine`; `TaskInputs.upstream() -> Any`; the module-private sentinel `_NO_UPSTREAM` and the defaulted field `TaskInputs._upstream`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_inputs.py`:

```python
from dataclasses import replace

import pytest

from sonata_engine import NoUpstreamValueError, TaskInputs


def test_upstream_raises_when_no_step_produced_a_value() -> None:
    with pytest.raises(NoUpstreamValueError, match="no upstream value"):
        TaskInputs.empty().upstream()


def test_upstream_returns_the_value_it_was_built_with() -> None:
    inputs = replace(TaskInputs.empty(), _upstream="rel-42")
    assert inputs.upstream() == "rel-42"


def test_upstream_returns_none_as_a_legitimate_value() -> None:
    """None is a value a step may return; it must not read as 'no upstream'."""
    inputs = replace(TaskInputs.empty(), _upstream=None)
    assert inputs.upstream() is None


def test_existing_construction_paths_still_work() -> None:
    """`empty()` and the two-argument constructor predate the new field."""
    for inputs in (TaskInputs.empty(), TaskInputs({}, frozenset())):
        with pytest.raises(NoUpstreamValueError):
            inputs.upstream()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_inputs.py -q`
Expected: FAIL with `ImportError: cannot import name 'NoUpstreamValueError'`.

- [ ] **Step 3: Add and export the error**

In `src/sonata_engine/errors.py`, add after `ResourceUnavailableError` (it has the same shape — a task asking for something that is not there):

```python
class NoUpstreamValueError(Exception):
    """Raised when a step asks for an upstream value and none precedes it."""
```

In `src/sonata_engine/__init__.py`, import `NoUpstreamValueError` from
`sonata_engine.errors` and add it to `__all__` between
`"MissingAcquireUnitError"` and `"Resource"`, following the existing
case-insensitive ordering.

- [ ] **Step 4: Add the field and the accessor**

In `src/sonata_engine/core/inputs.py`, import the new error alongside the existing two, then add the sentinel above `TaskInputs`:

```python
# Distinct from `None`, which is a legitimate and reconstructible step value.
_NO_UPSTREAM: Any = object()
```

Add the field to `TaskInputs`, after the two existing ones so the current positional constructor keeps working:

```python
    _upstream: Any = _NO_UPSTREAM
```

Then add the accessor:

```python
    def upstream(self) -> Any:
        """The value the preceding step produced.

        Raises when nothing precedes this step: the first step of a top-level
        composite, or a task not running inside one.
        """
        if self._upstream is _NO_UPSTREAM:
            raise NoUpstreamValueError("no upstream value: nothing ran before this step")
        return self._upstream
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_inputs.py -q`
Expected: PASS.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run pytest && uv run ruff check . && uv run basedpyright
git add src/sonata_engine/errors.py src/sonata_engine/core/inputs.py src/sonata_engine/__init__.py tests/core/test_inputs.py
git commit -m "Let a task read the value the step before it produced"
```

---

### Task 2: a task's own fingerprint payload

**Files:**
- Modify: `src/sonata_engine/core/task.py`
- Modify: `src/sonata_engine/core/compiled.py:63-72`
- Test: `tests/core/test_compiled.py`

**Interfaces:**
- Produces: `Task._fingerprint_payload() -> object`, defaulting to `None`; `ReusableTask._fingerprint_payload()` returning `self.reuse_key`. `CompiledWorkflow.fingerprint` calls it instead of testing `isinstance(..., ReusableTask)`.

This task is behaviour-preserving. Its test reconstructs the current canonical
single-task fingerprint independently, so it detects any accidental movement of
existing plain or reusable workflow fingerprints.

- [ ] **Step 1: Write the failing tests**

Add `hashlib` and `json` to `tests/core/test_compiled.py`, then add:

```python
def _legacy_single_task_fingerprint(
    task: Task[object], task_id: str, payload: object
) -> str:
    """The pre-refactor canonical form, kept here as the compatibility oracle."""
    topology = [
        (
            task_id,
            "consumer",
            f"{type(task).__module__}.{type(task).__qualname__}",
            payload,
            (),
        )
    ]
    canonical = json.dumps(
        ["w", topology],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def test_fingerprint_is_unchanged_for_a_task_contributing_no_payload() -> None:
    task = _NoopTask("Plain")
    workflow = Workflow(workflow_id="w")
    workflow.add(task)

    assert task._fingerprint_payload() is None
    assert workflow.compile().fingerprint == _legacy_single_task_fingerprint(
        task, "001.plain", None
    )


def test_reusable_task_still_contributes_its_reuse_key() -> None:
    task = _ReusableNoop("key-1")
    workflow = Workflow(workflow_id="w")
    workflow.add(task)

    assert task._fingerprint_payload() == "key-1"
    assert workflow.compile().fingerprint == _legacy_single_task_fingerprint(
        task, "001.build", "key-1"
    )


def test_a_payload_change_changes_the_fingerprint() -> None:
    class Payloaded(Task[None]):
        title = "Payloaded"

        def __init__(self, payload: object) -> None:
            self._payload = payload

        def _fingerprint_payload(self) -> object:
            return self._payload

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return TaskOutcome()

    def fingerprint_for(payload: object) -> str:
        workflow = Workflow(workflow_id="w")
        workflow.add(Payloaded(payload))
        return workflow.compile().fingerprint

    assert fingerprint_for(["a"]) != fingerprint_for(["b"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_compiled.py -q`
Expected: FAIL with
`AttributeError: '_NoopTask' object has no attribute '_fingerprint_payload'`.

- [ ] **Step 3: Add the default and the reusable override**

In `src/sonata_engine/core/task.py`, add to `Task` (not abstract — the default is the point):

```python
    def _fingerprint_payload(self) -> object:
        """What this task contributes to the workflow's resume fingerprint.

        `None` for a task whose identity is fully described by its class and
        position. A task that owns children returns something covering them, so
        editing them invalidates resume. Must be JSON-serializable.
        """
        return None
```

Add to `ReusableTask`, replacing what `compiled.py` used to reach in for:

```python
    @override
    def _fingerprint_payload(self) -> object:
        return self.reuse_key
```

- [ ] **Step 4: Make the fingerprint consume it**

In `src/sonata_engine/core/compiled.py`, inside the `topology` comprehension, replace this line:

```python
                task.task.reuse_key if isinstance(task.task, ReusableTask) else None,
```

with:

```python
                task.task._fingerprint_payload(),
```

Remove the now-unused `ReusableTask` import from that file if nothing else uses it — check with `grep -n ReusableTask src/sonata_engine/core/compiled.py` before deleting.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_compiled.py -q`
Expected: PASS. Then `uv run pytest -q` — every existing journal and resume test must still pass, which is what proves the refactor preserved behaviour.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run pytest && uv run ruff check . && uv run basedpyright
git add src/sonata_engine/core/task.py src/sonata_engine/core/compiled.py tests/core/test_compiled.py
git commit -m "Let a task say what it contributes to the resume fingerprint"
```

---

### Task 3: one shared recorded-execution routine

**Files:**
- Modify: `src/sonata_engine/journal.py:299-307`
- Modify: `src/sonata_engine/core/workflow.py:234-295`

**Interfaces:**
- Produces: `Journal.decide_task(task_id: str, task: Task[Any]) -> ResumeAction`; the module-level `_execute_recorded(...) -> TaskExecution` in `core/workflow.py`, whose signature Task 4 calls.

Pure refactor. `_run_unit` keeps its behaviour exactly; the existing suite is the proof.

`_run_unit` currently builds inputs inside `_task_lifecycle`. Keep that ordering
by passing a callable to the extracted routine. No new characterization test is
needed: the proposed undeclared-resource test would fail inside `task.run()`,
not while building inputs, and would not prove this ordering.

- [ ] **Step 1: Establish the refactor baseline**

Run: `uv run pytest -q`
Expected: PASS, 185 tests: the branch baseline of 178 plus the four tests from
Task 1 and the three from Task 2. If the count differs, stop and explain it
before changing the execution path.

- [ ] **Step 2: Generalize the journal's resume decision**

In `src/sonata_engine/journal.py`, replace the body of `decide` and add the general form beside it:

```python
    def decide(self, compiled_task: CompiledTask[object]) -> ResumeAction:
        """Resume decision for a consumer task (may raise `AmbiguousTaskStateError`)."""
        return self.decide_task(compiled_task.task_id, compiled_task.task)

    def decide_task(self, task_id: str, task: Task[Any]) -> ResumeAction:
        """Resume decision for anything with a journal identity: a compiled unit
        or one step of a composite."""
        return decide_resume(
            self._states.get(task_id),
            idempotent=task.idempotent,
            reusable=isinstance(task, ReusableTask),
            verifiers=self.verifiers,
        )
```

Add `Task` and `Any` to that module's imports if absent.

- [ ] **Step 3: Extract the routine**

In `src/sonata_engine/core/workflow.py`, add this module-level function above the `Workflow` class:

```python
def _execute_recorded(
    *,
    task: Task[Any],
    task_id: str,
    make_inputs: Callable[[], TaskInputs],
    jrnl: Journal | None,
    resume: bool,
    on_outcome: Callable[[TaskOutcome[Any]], None] | None = None,
) -> TaskExecution:
    """Run one thing that has a journal identity, recording its outcome.

    Shared by compiled consumer/acquire units and by the steps of a composite,
    so the resume decision, the started/passed/failed records, the outcome
    validation and the journal-failure note exist once. Release units keep
    their own path in `_release`: cleanup must not abort on a journal error.

    `make_inputs` is a callable, not a value, because it is called inside the
    lifecycle — a failure resolving resources must be reported as a task
    failure, not escape unreported.
    """
    if jrnl is not None and resume:
        if jrnl.decide_task(task_id, task) == "skip":
            jrnl.record_skipped(task_id, jrnl.next_attempt(task_id))
            _task_skipped(task_id=task_id, title=task.title)
            return TaskExecution(task_id=task_id, status="skipped", outcome=None)

    attempt = jrnl.next_attempt(task_id) if jrnl is not None else 0
    if jrnl is not None:
        # Flush the started record BEFORE executing, so a crash mid-task is durable.
        jrnl.record_started(task_id, attempt)
    try:
        with _task_lifecycle(task_id=task_id, title=task.title):
            outcome = task.run(make_inputs())
            if not isinstance(outcome, TaskOutcome):
                raise InvalidTaskOutcomeError(
                    f"{task_id} returned {outcome!r}, expected TaskOutcome"
                )
            if isinstance(task, ReusableTask) and outcome.value is not None:
                raise InvalidTaskOutcomeError(
                    f"{task_id} is reusable and returned a runtime value"
                )
            if on_outcome is not None:
                on_outcome(outcome)
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
```

Then replace the body of `_run_unit` (keep its signature and docstring) with:

```python
        def make_inputs() -> TaskInputs:
            return state.inputs_for(
                compiled_task.resource.requires
                if compiled_task.kind == "acquire" and compiled_task.resource is not None
                else compiled_task.required_resources
            )

        def on_outcome(outcome: TaskOutcome[Any]) -> None:
            if compiled_task.kind == "acquire" and compiled_task.resource is not None:
                state.publish(compiled_task.resource, outcome.value)
            if on_executed is not None:
                on_executed()

        return _execute_recorded(
            task=compiled_task.task,
            task_id=compiled_task.task_id,
            make_inputs=make_inputs,
            jrnl=jrnl,
            resume=resume,
            on_outcome=on_outcome,
        )
```

`self._next_attempt` may now be unused — check with `grep -n _next_attempt src/sonata_engine/core/workflow.py`; `_release` still calls it, so expect it to stay.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, unchanged count. Every journal, resume and failure test passing untouched is the proof this refactor changed nothing.

- [ ] **Step 5: Run the gates and commit**

```bash
uv run pytest && uv run ruff check . && uv run basedpyright
git add src/sonata_engine/journal.py src/sonata_engine/core/workflow.py
git commit -m "Extract the one routine that runs and records a journalled task"
```

---

### Task 4: the step scope

**Files:**
- Create: `src/sonata_engine/core/step_scope.py`
- Modify: `src/sonata_engine/core/inputs.py`
- Modify: `src/sonata_engine/core/workflow.py` (the `make_inputs` added in Task 3)
- Test: `tests/core/test_step_scope.py`

**Interfaces:**
- Consumes: `_execute_recorded` from Task 3; `TaskInputs._upstream` from Task 1.
- Produces: the `StepScope` protocol with `run_step(step: Task[Any], slug: str, upstream: Any) -> TaskExecution`; the field `TaskInputs._step_scope: StepScope | None`; the runner attaching a scope to every unit's inputs.

The protocol lives in its own module so `inputs.py` can type the field and `workflow.py` can implement it without an import cycle.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_step_scope.py`:

```python
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import pytest

from sonata_engine import Resource, Task, TaskInputs, TaskOutcome, Workflow
from sonata_engine.workflow.context import bind_workflow_sink
from sonata_engine.workflow.events import WorkflowEvent


class _FakeSink:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)

    @contextmanager
    def status(self, label: str) -> Generator[None, None, None]:
        yield


class _Echo(Task[str]):
    title = "Echo"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value="echoed")


def test_a_unit_receives_a_step_scope() -> None:
    """The runner attaches one to every unit, so any task can be a composite."""
    seen: list[object] = []

    class Peeker(Task[None]):
        title = "Peeker"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            seen.append(inputs._step_scope)
            return TaskOutcome()

    workflow = Workflow(workflow_id="w")
    workflow.add(Peeker())
    workflow.run()

    assert seen[0] is not None


def test_the_scope_names_a_step_under_the_compiled_unit_id() -> None:
    ids: list[str] = []

    class Runner(Task[None]):
        title = "Runner"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            scope = inputs._step_scope
            assert scope is not None
            ids.append(scope.run_step(_Echo(), "echo", upstream=None).task_id)
            return TaskOutcome()

    workflow = Workflow(workflow_id="w")
    workflow.add(Runner())

    sink = _FakeSink()
    with bind_workflow_sink(sink):
        workflow.run()

    assert ids == ["001.runner/echo"]
    started = [e.task_id for e in sink.events if e.kind == "task.started"]
    assert started == ["001.runner", "001.runner/echo"]
    child = next(e for e in sink.events if e.task_id == "001.runner/echo")
    assert child.parent_task_id == "001.runner"


def test_the_step_receives_the_upstream_and_the_composite_resources() -> None:
    seen: list[object] = []
    resource = Resource[str](
        title="Acquire token",
        acquire=lambda _inputs: "token-42",
        release=lambda _inputs, _value: None,
    )

    class Reader(Task[None]):
        title = "Reader"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            seen.append((inputs.upstream(), inputs.resource(resource)))
            return TaskOutcome()

    class Runner(Task[None]):
        title = "Runner"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            scope = inputs._step_scope
            assert scope is not None
            scope.run_step(Reader(), "reader", upstream="rel-42")
            return TaskOutcome()

    workflow = Workflow(workflow_id="w")
    workflow.add(Runner(), requires=(resource,))
    workflow.run()

    assert seen == [("rel-42", "token-42")]


def test_a_step_gets_its_own_nested_scope() -> None:
    """So a composite among the steps nests one level further."""
    ids: list[str] = []

    class Inner(Task[None]):
        title = "Inner"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            scope = inputs._step_scope
            assert scope is not None
            ids.append(scope.run_step(_Echo(), "echo", upstream=None).task_id)
            return TaskOutcome()

    class Outer(Task[None]):
        title = "Outer"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            scope = inputs._step_scope
            assert scope is not None
            scope.run_step(Inner(), "inner", upstream=None)
            return TaskOutcome()

    workflow = Workflow(workflow_id="w")
    workflow.add(Outer())

    sink = _FakeSink()
    with bind_workflow_sink(sink):
        workflow.run()

    assert ids == ["001.outer/inner/echo"]
    echo = next(
        event
        for event in sink.events
        if event.kind == "task.started" and event.task_id == "001.outer/inner/echo"
    )
    assert echo.parent_task_id == "001.outer/inner"


def test_a_failing_step_emits_child_and_parent_failures() -> None:
    class Boom(Task[None]):
        title = "Boom"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            raise RuntimeError("boom")

    class Outer(Task[None]):
        title = "Outer"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            scope = inputs._step_scope
            assert scope is not None
            scope.run_step(Boom(), "boom", upstream=None)
            return TaskOutcome()

    workflow = Workflow(workflow_id="w")
    workflow.add(Outer())
    sink = _FakeSink()

    with bind_workflow_sink(sink), pytest.raises(RuntimeError, match="boom"):
        workflow.run()

    failed = [event.task_id for event in sink.events if event.kind == "task.failed"]
    assert failed == ["001.outer/boom", "001.outer"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_step_scope.py -q`
Expected: FAIL with `AttributeError: 'TaskInputs' object has no attribute '_step_scope'`.

- [ ] **Step 3: Add the protocol**

Create `src/sonata_engine/core/step_scope.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sonata_engine.core.compiled import TaskExecution
    from sonata_engine.core.task import Task


class StepScope(Protocol):
    """What a composite is given so it can run its steps without knowing where
    it sits.

    The runner builds it, so it holds the compiled unit id, the journal and the
    resume flag — none of which a task is told. A composite passes a step and a
    slug; the scope names it, decides, records, reports and runs it.
    """

    def run_step(self, step: Task[Any], slug: str, upstream: Any) -> TaskExecution:
        """Run one step beneath this scope and return how it went."""
        ...
```

- [ ] **Step 4: Carry it on `TaskInputs`**

In `src/sonata_engine/core/inputs.py`, change the dataclasses import to
`from dataclasses import dataclass, field`, then add to the `TYPE_CHECKING`
block:

```python
    from sonata_engine.core.step_scope import StepScope
```

and add the field after `_upstream`. The scope is engine plumbing, so it must
not affect `TaskInputs` equality or appear in its representation:

```python
    _step_scope: StepScope | None = field(default=None, compare=False, repr=False)
```

`Steps` reads this attribute directly. It is underscore-private and stays that way: it is engine plumbing, not something a hand-written task should reach for.

- [ ] **Step 5: Implement it in the runner**

In `src/sonata_engine/core/workflow.py`, add the implementation above the `Workflow` class:

```python
@dataclass(frozen=True, slots=True)
class _StepScope:
    """The runner's `StepScope`: everything a composite must not know."""

    prefix: str
    base_inputs: TaskInputs
    jrnl: Journal | None
    resume: bool

    def run_step(self, step: Task[Any], slug: str, upstream: Any) -> TaskExecution:
        step_id = f"{self.prefix}/{slug}"

        def make_inputs() -> TaskInputs:
            return replace(
                self.base_inputs,
                _upstream=upstream,
                _step_scope=replace(self, prefix=step_id),
            )

        return _execute_recorded(
            task=step,
            task_id=step_id,
            make_inputs=make_inputs,
            jrnl=self.jrnl,
            resume=self.resume,
        )
```

Add `from dataclasses import dataclass, replace` and `from typing import Any` to that module's imports if absent.

Then attach a scope to every unit's inputs, by replacing the `make_inputs` body added in Task 3:

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

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_step_scope.py -q`
Expected: PASS, all five.

- [ ] **Step 7: Run the gates and commit**

```bash
uv run pytest && uv run ruff check . && uv run basedpyright
git add src/sonata_engine/core/step_scope.py src/sonata_engine/core/inputs.py src/sonata_engine/core/workflow.py tests/core/test_step_scope.py
git commit -m "Give every unit a scope it can run named, journalled steps in"
```

---

### Task 5: `Steps`

**Files:**
- Create: `src/sonata_engine/core/steps.py`
- Modify: `src/sonata_engine/core/__init__.py`
- Modify: `src/sonata_engine/__init__.py`
- Test: `tests/core/test_steps.py`

**Interfaces:**
- Consumes: `StepScope.run_step` from Task 4; `TaskInputs.upstream()` from Task 1; `Task._fingerprint_payload` from Task 2.
- Produces: `Steps(*, title: str, steps: tuple[Task[Any], ...])`, exported from `sonata_engine`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_steps.py`:

```python
from __future__ import annotations

import pytest

from sonata_engine import (
    ReusableTask,
    Selection,
    Steps,
    Task,
    TaskInputs,
    TaskOutcome,
    Workflow,
)
from sonata_engine.errors import InvalidTaskOutcomeError, NoUpstreamValueError


class _Produce(Task[str]):
    def __init__(self, title: str, value: str) -> None:
        self.title = title
        self._value = value

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value=self._value)


class _Forward(Task[str]):
    title = "Forward"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value=inputs.upstream())


class _Decorate(Task[str]):
    title = "Decorate"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value=f"http://{inputs.upstream()}.svc")


def _run(*steps: Task[object], title: str = "Deploy") -> object:
    workflow = Workflow(workflow_id="w")
    workflow.add(Steps(title=title, steps=tuple(steps)))
    execution = workflow.run().tasks[0]
    assert execution.outcome is not None
    return execution.outcome.value


def test_a_value_flows_through_the_pipeline() -> None:
    assert _run(_Produce("Install", "rel-42"), _Forward(), _Decorate()) == "http://rel-42.svc"


def test_none_flows_as_a_legitimate_value() -> None:
    class ProduceNone(Task[None]):
        title = "Produce none"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return TaskOutcome()

    class AssertNone(Task[str]):
        title = "Assert none"

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            assert inputs.upstream() is None
            return TaskOutcome(value="ok")

    assert _run(ProduceNone(), AssertNone()) == "ok"


def test_upstream_raises_in_the_first_step_of_a_top_level_composite() -> None:
    with pytest.raises(NoUpstreamValueError):
        _run(_Forward())


def test_a_nested_composite_receives_the_outer_upstream() -> None:
    """Composition is transparent: the inner pipeline continues the outer one."""
    inner = Steps(title="Inner", steps=(_Forward(),))
    assert _run(_Produce("Install", "rel-42"), inner) == "rel-42"


def test_a_composite_compiles_to_one_unit_and_selection_keeps_it_whole() -> None:
    workflow = Workflow(workflow_id="w")
    workflow.add(Steps(title="Deploy", steps=(_Produce("Install", "rel-42"), _Decorate())))

    compiled = workflow.compile()
    assert [task.task_id for task in compiled.tasks] == ["001.deploy"]

    selected = workflow.compile(select=Selection(only="deploy"))
    assert [task.task_id for task in selected.tasks] == ["001.deploy"]


def test_an_already_written_task_serves_as_a_step_unmodified() -> None:
    """The central promise: nothing about Task changed to make this work."""

    class WrittenBefore(Task[str]):
        title = "Written before"

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            return TaskOutcome(value="unchanged")

    assert _run(WrittenBefore()) == "unchanged"


def test_construction_rejects_an_empty_step_list() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        Steps(title="Deploy", steps=())


def test_construction_rejects_duplicate_normalized_slugs() -> None:
    """'Build A' and 'Build-A' normalize to the same journal identity."""
    with pytest.raises(ValueError, match="duplicate step slug 'build-a'"):
        Steps(title="Deploy", steps=(_Produce("Build A", "1"), _Produce("Build-A", "2")))


def test_construction_rejects_a_title_that_normalizes_to_nothing() -> None:
    with pytest.raises(ValueError, match="produces an empty slug"):
        Steps(title="Deploy", steps=(_Produce("---", "1"),))


def test_a_step_returning_a_non_outcome_raises() -> None:
    class Bad(Task[None]):
        title = "Bad"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return "not-an-outcome"  # type: ignore[return-value]

    with pytest.raises(InvalidTaskOutcomeError):
        _run(Bad())


def test_a_reusable_step_returning_a_value_raises() -> None:
    """Inherited from the shared executor, not restated in Steps."""

    class BadReusable(ReusableTask):
        title = "Bad reusable"
        reuse_key = "bad"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return TaskOutcome(value=42)  # type: ignore[arg-type]

    with pytest.raises(InvalidTaskOutcomeError):
        _run(BadReusable())


def test_steps_is_idempotent_so_a_failed_unit_can_be_re_entered() -> None:
    assert Steps(title="Deploy", steps=(_Forward(),)).idempotent is True


def test_the_fingerprint_payload_covers_the_steps() -> None:
    def payload(*steps: Task[object]) -> object:
        return Steps(title="Deploy", steps=tuple(steps))._fingerprint_payload()

    class OtherProduce(Task[str]):
        title = "Install"

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            return TaskOutcome(value="1")

    class Reusable(ReusableTask):
        title = "Build"

        def __init__(self, key: str) -> None:
            self._key = key

        @property
        def reuse_key(self) -> str:
            return self._key

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            return TaskOutcome()

    a = _Produce("Install", "1")
    b = _Produce("Resolve", "2")
    assert payload(a, b) != payload(b, a)
    assert payload(a) != payload(a, b)
    assert payload(_Produce("Install", "1")) != payload(_Produce("Resolve", "1"))
    assert payload(_Produce("Install", "1")) != payload(OtherProduce())
    assert payload(Reusable("key-1")) != payload(Reusable("key-2"))

    nested_a = Steps(title="Inner", steps=(_Produce("Install", "1"),))
    nested_b = Steps(title="Inner", steps=(_Produce("Resolve", "1"),))
    assert payload(nested_a) != payload(nested_b)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_steps.py -q`
Expected: FAIL with `ImportError: cannot import name 'Steps' from 'sonata_engine'`.

- [ ] **Step 3: Implement `Steps`**

Create `src/sonata_engine/core/steps.py`:

```python
from __future__ import annotations

from typing import Any, override

from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import Task


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
        # Imported here: `core.workflow` imports this module's package, and
        # `_slugify` lives beside the compiler that owns slug shape.
        from sonata_engine.core.workflow import _slugify

        if not steps:
            raise ValueError("Steps requires at least one step")

        slugs: list[str] = []
        for step in steps:
            slug = _slugify(step.title)
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
            for slug, step in zip(self._slugs, self._steps)
        )

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[Any]:
        scope = inputs._step_scope
        if scope is None:
            raise RuntimeError(
                f"{type(self).__name__} {self.title!r} must be run by the workflow "
                "runner: add it to a Workflow rather than calling run() directly"
            )

        upstream: Any = inputs._upstream
        for slug, step in zip(self._slugs, self._steps):
            execution = scope.run_step(step, slug, upstream)
            # A step that ran contributes its value, including a legitimate
            # None; a skipped step contributes None, the only value a skippable
            # ReusableTask may return.
            upstream = execution.outcome.value if execution.outcome is not None else None
        return TaskOutcome(value=upstream)
```

Note `upstream` starts from `inputs._upstream`, not from `_NO_UPSTREAM`: a top-level composite receives the sentinel and its first step's `upstream()` raises, while a nested composite receives the outer value and passes it straight through.

- [ ] **Step 4: Export it**

In `src/sonata_engine/core/__init__.py`, add `Steps` to the imports and `__all__` in the position the file's existing order dictates.

In `src/sonata_engine/__init__.py`, add `Steps` to the
`from sonata_engine.core import (...)` block and to `__all__`, inserting it
between `"status"` and `"subtask"` in the existing case-insensitive order
without reordering anything else.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_steps.py -q`
Expected: PASS.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run pytest && uv run ruff check . && uv run basedpyright
git add src/sonata_engine/core/steps.py src/sonata_engine/core/__init__.py src/sonata_engine/__init__.py tests/core/test_steps.py
git commit -m "Assemble a task from steps instead of writing its loop"
```

---

### Task 6: resume across steps, and the documentation

**Files:**
- Test: `tests/core/test_steps_resume.py`
- Modify: `README.md`
- Modify: `examples/demo_workflow.py`
- Modify: `tests/test_examples.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5. Produces nothing other tasks use.

This is the payoff task: it proves the thing the journal work exists for.

- [ ] **Step 1: Write the integration tests**

Create `tests/core/test_steps_resume.py`. Look at how the existing journal tests build a `JournalConfig` (`grep -rn "JournalConfig(" tests/`) and follow that pattern for the temporary path.

```python
from __future__ import annotations

import pytest

from sonata_engine import (
    Evidence,
    JournalConfig,
    ReusableTask,
    Steps,
    Task,
    TaskInputs,
    TaskOutcome,
    Workflow,
)
from sonata_engine.errors import AmbiguousTaskStateError, WorkflowTopologyMismatchError
from sonata_engine.journal import Journal

ALWAYS = {"always": lambda _e: True}


class _Reusable(ReusableTask):
    """Verified evidence lets resume skip it and reconstruct its value as None."""

    def __init__(self, title: str, ran: list[str]) -> None:
        self.title = title
        self._ran = ran

    @property
    def reuse_key(self) -> str:
        return self.title

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        self._ran.append(self.title)
        return TaskOutcome(evidence=(Evidence("always", self.title),))


class _RetryableBuild(ReusableTask):
    title = "Build app"
    idempotent = True
    reuse_key = "build-app-v1"

    def __init__(self, ran: list[str], attempts: list[int]) -> None:
        self._ran = ran
        self._attempts = attempts

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        # On the resumed run the preceding reusable step is skipped. Its only
        # legal value, None, must still be reconstructed as this step's upstream.
        assert inputs.upstream() is None
        self._ran.append(self.title)
        self._attempts[0] += 1
        if self._attempts[0] == 1:
            raise RuntimeError("boom")
        return TaskOutcome(evidence=(Evidence("always", self.title),))


def _workflow(ran: list[str], attempts: list[int]) -> Workflow:
    workflow = Workflow(workflow_id="w")
    workflow.add(
        Steps(
            title="Publish",
            steps=(
                _Reusable("Build cp", ran),
                _Reusable("Build fn", ran),
                _RetryableBuild(ran, attempts),
            ),
        )
    )
    return workflow


def test_resume_skips_finished_reusable_steps_and_retries_the_failed_one(
    tmp_path,
) -> None:
    """The point of the journal work: the last build fails, you resume, the
    earlier builds do not run again."""
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    ran: list[str] = []
    attempts = [0]

    with pytest.raises(RuntimeError, match="boom"):
        _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)
    assert ran == ["Build cp", "Build fn", "Build app"]

    ran.clear()
    _workflow(ran, attempts).run(journal=config, resume=True, verifiers=ALWAYS)
    assert ran == ["Build app"]
    assert attempts == [2]


def test_resume_false_runs_every_step_even_with_a_journal(tmp_path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    ran: list[str] = []
    attempts = [1]  # the retryable build succeeds on every run in this test
    _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)
    assert ran == ["Build cp", "Build fn", "Build app"]

    ran.clear()
    _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)
    assert ran == ["Build cp", "Build fn", "Build app"]


def test_an_interrupted_non_idempotent_step_refuses_to_resume(tmp_path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")

    class Fragile(Task[str]):
        title = "Fragile"
        idempotent = False

        def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
            raise AssertionError("an interrupted non-idempotent step must not run")

    def build() -> Workflow:
        workflow = Workflow(workflow_id="w")
        workflow.add(Steps(title="Publish", steps=(Fragile(),)))
        return workflow

    # Simulate a process stopping after the durable child `started` record but
    # before any terminal record. The enclosing unit remains `pending`, so the
    # resume reaches the child decision.
    seeded = build()
    Journal(config, seeded.compile()).record_started("001.publish/fragile", 1)

    with pytest.raises(AmbiguousTaskStateError):
        build().run(journal=config, resume=True, verifiers=ALWAYS)


def test_changing_the_step_list_invalidates_resume(tmp_path) -> None:
    config = JournalConfig(path=tmp_path / "journal.jsonl")
    ran: list[str] = []
    attempts = [0]
    with pytest.raises(RuntimeError):
        _workflow(ran, attempts).run(journal=config, verifiers=ALWAYS)

    changed = Workflow(workflow_id="w")
    changed.add(
        Steps(
            title="Publish",
            steps=(
                _Reusable("Build fn", ran),
                _Reusable("Build cp", ran),
                _RetryableBuild(ran, attempts),
            ),
        )
    )
    with pytest.raises(WorkflowTopologyMismatchError):
        changed.run(journal=config, resume=True, verifiers=ALWAYS)
```

`Evidence` is `(kind, reference, digest=None)` and a verifier is `Callable[[Evidence], bool]` keyed by evidence kind — see `tests/test_resume.py:167` for the shape this mirrors.

- [ ] **Step 2: Run the integration tests**

Run: `uv run pytest tests/core/test_steps_resume.py -q`
Expected: PASS, all four. Tasks 1-5 already implement the behavior under test;
this task is their integration proof. The persistent `attempts` list makes
“fail once, then pass” deterministic across the two workflow instances; do not
couple retry state to the `ran` list that the assertions clear.

- [ ] **Step 3: Investigate any integration failure**

No new production code should be needed: Tasks 1-5 implement all of this. If a
test fails, first verify the plan was followed in order. If behavior is really
missing, fix it in the module that owns it rather than working around it in the
test, and note what was missing in your report.

- [ ] **Step 4: Document it in the README**

In `README.md`, add a `##` section after `## Reporting steps inside a task` (the section `subtask` added) and before `## Selecting a slice`:

```markdown
## Assembling a task from steps

A task made of steps does not need a `run()` of its own. `Steps` takes them and
runs them in order, feeding each the value the one before produced:

```python
from sonata_engine import Steps

workflow.add(
    Steps(
        title="Deploy the chart",
        steps=(HelmInstall(chart), WaitRollout(), ResolveEndpoint()),
    )
)
```

Each step is an ordinary `Task`, so anything already written serves as one, and
a step that needs no input simply never calls `inputs.upstream()`. The engine
names the steps under the compiled unit — `001.deploy-the-chart/install-chart` —
so you choose titles and nothing else.

The composite stays one compiled unit: one ordinal, one thing `Selection` can
name, one fate. But its steps are journalled individually, so a resumed run
skips the ones already finished. What may be skipped is decided exactly as for a
compiled unit: a `ReusableTask` whose evidence still verifies. Its only legal
value is `None`, which the engine reconstructs when the step is skipped — build
five images, have the fifth fail, resume, and only the fifth runs again.
```

- [ ] **Step 5: Show it in the demo**

In `examples/demo_workflow.py`, replace the hand-written `BuildImages` class with one task per unit of work and a `Steps` that assembles them. Keep `ConsoleSink`, `PrepareSource`, the resource and `PushImages` exactly as they are.

Delete `BuildImages` and add:

```python
class BuildImage(Task[tuple[str, ...]]):
    def __init__(self, image: str) -> None:
        self.title = f"Build {image}"
        self._image = image

    def run(self, inputs: TaskInputs) -> TaskOutcome[tuple[str, ...]]:
        workflow_log(f"docker build {self._image}")
        try:
            built = inputs.upstream()
        except NoUpstreamValueError:
            built = ()
        return TaskOutcome(value=(*built, f"registry.example/{self._image}:v1"))


class ScanImages(Task[tuple[str, ...]]):
    title = "Scan for vulnerabilities"

    def run(self, inputs: TaskInputs) -> TaskOutcome[tuple[str, ...]]:
        built = inputs.upstream()
        workflow_log(f"scanning {len(built)} images")
        return TaskOutcome(value=built)
```

Import `NoUpstreamValueError` and `Steps` from `sonata_engine`, and drop
`subtask`. The same `BuildImage` class can occupy either position: the first
instance catches the explicit “no predecessor” error and starts the tuple;
later instances extend the upstream tuple.

In `main()`, replace the `workflow.add(BuildImages(...))` line with:

```python
    workflow.add(
        Steps(
            title="Build images",
            steps=(*(BuildImage(image) for image in IMAGES), ScanImages()),
        )
    )
```

- [ ] **Step 6: Update the example test for the new ids**

The ids change, because the engine now derives them: `build-images/build/control-plane` becomes `002.build-images/build-control-plane`. In `tests/test_examples.py`, update `test_demo_workflow_example_reports_subtasks_without_compiling_them` to the new shape while keeping the invariant it exists for — step ids appear in the event stream and stay out of the compiled unit list:

```python
def test_demo_workflow_example_reports_steps_without_compiling_them() -> None:
    """The demo is where the composite contract is visible end to end: the steps
    appear in the event stream, and the compiled unit list is unaffected."""
    stdout = _run_demo().stdout
    events, _, compiled = stdout.partition("Compiled task IDs and outcomes:")

    assert "002.build-images/build-control-plane" in events
    assert "002.build-images/scan-for-vulnerabilities" in events
    assert "002.build-images/" not in compiled, (
        "a step id reached the compiled unit list:\n" + compiled
    )
    assert "002.build-images" in compiled
```

Run `uv run python examples/demo_workflow.py` and read the output before trusting these strings: if a title slugifies differently than written here, follow the actual output rather than editing the slugifier.

- [ ] **Step 7: Run everything and commit**

```bash
uv run pytest && uv run ruff check . && uv run basedpyright
uv run python examples/demo_workflow.py
git add tests/core/test_steps_resume.py README.md examples/demo_workflow.py tests/test_examples.py
git commit -m "Resume a composite without redoing the steps it finished"
```

---

## Self-review notes

- **Spec coverage.** "One construct, always sequential" → Task 5. "Data between
  steps" → Tasks 1 and 5. "Identity: the engine names the steps" → Task 4.
  "The journal, per step" → Tasks 2, 3, 4 and 6. "Why resume and data flow do
  not collide" → Task 6, including reconstruction of `None` after a skipped
  reusable step and refusal to rerun an interrupted non-idempotent child. Task
  4 exercises real composite resources, nested parent ids, and child/parent
  failure events. Task 5 covers fingerprint changes caused by order, length,
  slug, task class, reuse key, and nested child topology. Every row of the
  spec's errors table has a test in Task 5 except the resume rows, which are in
  Task 6.
- **The refactor is load-bearing and invisible.** Task 3 changes no behaviour, and its only proof is that the existing suite stays green. If it goes wrong, everything after it inherits the damage — so treat an unexpected failure there as a stop, not a nuisance.
- **`Steps.idempotent = True` is not a detail.** The review of the spec caught that without it the enclosing unit's failed record raises before `Steps.run()` is ever entered, which would make the per-step journal unreachable on exactly the resume it exists for. Task 5 asserts it directly.
- **Deliberately not here.** No parallel composite, no step-level `Selection`, no step declaring its own resources. All three are stated limitations in the spec, not oversights.
