# Subtask Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a task report the steps it performs inside its own `run()`, so a step made of many appears as many in the event stream without becoming many compiled units.

**Architecture:** `_task_lifecycle` already binds a context carrying the running task's id, and `_child_context` already resolves an absent parent from that context. Nesting therefore needs no new plumbing — only a public entry point. This adds one context manager, `subtask`, which delegates to `_task_lifecycle` and exports it. Delegation rather than a second copy of the body: the difference between the two is that `subtask` is public and exposes no `context` override, and both of those are properties of the signature, not of the body.

**Tech Stack:** Python 3.12+, standard library only, pytest, Ruff, Basedpyright.

**Spec:** `docs/specs/2026-07-27-subtask-reporting-design.md`

## Global Constraints

- Sonata takes no runtime dependencies and never imports from a downstream product. `tests/test_package_boundaries.py` enforces this.
- The compiler owns compiled-unit identity. `subtask` must never assign, derive, or renumber a compiled `NNN.slug` id.
- Selection, ordinals, the journal and `TaskInputs` are untouched by this change. A workflow whose tasks open subtasks must compile to exactly the same unit list as one whose tasks do not.
- No new event kinds. Four exist: `task.started`, `task.passed`, `task.failed` and `task.skipped` (emitted by `_task_skipped`). `subtask` emits the first three and never the fourth, which stays runner-owned — a subtask is either entered or it is not.
- Coverage gate and lint settings in `pyproject.toml` apply as they stand; do not relax them.

---

### Task 1: The `subtask` context manager

**Files:**
- Modify: `src/sonata_engine/workflow/reporting.py`
- Test: `tests/workflow/test_reporting.py`

**Interfaces:**
- Consumes: `_task_lifecycle`, already in `reporting.py`.
- Produces: `subtask(*, task_id: str, title: str = "") -> AbstractContextManager[None]`, later exported from `sonata_engine`.

- [x] **Step 1: Write the failing tests**

Add to `tests/workflow/test_reporting.py`. `_FakeSink` and the imports of `bind_workflow_sink` already exist at the top of that file; add `subtask` and `bind_workflow_context` to the existing import lines.

Note the two id shapes below: the bound parent contexts carry `NNN.slug` ids because the compiler assigned those, while every id passed to `subtask` is a plain caller-chosen slug. That asymmetry is the contract, so the examples should show it.

```python
def test_subtask_emits_started_and_passed() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with subtask(task_id="build-images/cp", title="Build control plane"):
            pass

    assert [event.kind for event in sink.events] == ["task.started", "task.passed"]
    assert sink.events[0].task_id == "build-images/cp"
    assert sink.events[0].title == "Build control plane"


def test_subtask_nests_under_the_task_that_is_running() -> None:
    """The parent is read from the bound context, not passed in: a task does not
    know the id the compiler gave it."""
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with bind_workflow_context(WorkflowContext(task_id="003.build-images")):
            with subtask(task_id="build-images/cp", title="cp"):
                pass

    assert {event.parent_task_id for event in sink.events} == {"003.build-images"}


def test_subtasks_nest_to_whatever_depth_the_call_stack_produces() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with bind_workflow_context(WorkflowContext(task_id="001.outer")):
            with subtask(task_id="outer/mid", title="mid"):
                with subtask(task_id="outer/mid/inner", title="inner"):
                    pass

    parents = {event.task_id: event.parent_task_id for event in sink.events}
    assert parents["outer/mid"] == "001.outer"
    assert parents["outer/mid/inner"] == "outer/mid"


def test_a_failing_subtask_reports_and_still_propagates() -> None:
    """Reporting must not swallow the failure: the enclosing unit has to fail."""
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with pytest.raises(RuntimeError, match="image build failed"):
            with subtask(task_id="build-images/cp", title="cp"):
                raise RuntimeError("image build failed")

    assert [event.kind for event in sink.events] == ["task.started", "task.failed"]
    assert "image build failed" in sink.events[1].detail


def test_subtask_is_a_noop_without_a_sink() -> None:
    with subtask(task_id="build-images/cp", title="cp"):
        pass  # no error, no crash
```

Add the imports this needs to the top of the file:

```python
import pytest

from sonata_engine.workflow.context import bind_workflow_context, bind_workflow_sink
from sonata_engine.workflow.events import WorkflowContext, WorkflowEvent
from sonata_engine.workflow.reporting import status, subtask, workflow_log
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_reporting.py -q`
Expected: FAIL with `ImportError: cannot import name 'subtask'`.

- [x] **Step 3: Implement it**

In `src/sonata_engine/workflow/reporting.py`, add `subtask` immediately after `_task_lifecycle`. It delegates rather than repeating the body: the emit sequence, the `_child_context` fallback and the `add_note` handling for a sink that fails while reporting a failure are the delicate part of `_task_lifecycle`, and a second copy of them would have nothing keeping it in step.

```python
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
```

Taking no `context` parameter is the whole of the "runner only" restriction being lifted safely: a caller inside a running task cannot reparent itself somewhere else, because it has no way to pass a context in.

While here, add the task id to `_task_lifecycle`'s reporting-failure note, which today identifies neither the unit nor the subtask it came from. Replace:

```python
                exc.add_note(f"Failed to emit task.failed: {reporting_error}")
```

with:

```python
                exc.add_note(f"Failed to emit task.failed for {task_id}: {reporting_error}")
```

`test_failed_event_error_does_not_mask_task_root_cause` in `tests/core/test_workflow.py` asserts only that the inner error survives in the note, so it keeps passing.

Then revise `_task_lifecycle`'s docstring, whose second paragraph is now wrong. Replace:

```
    Used exclusively by the compiled-task runner, so lifecycle identity for a
    `CompiledTask` is owned by the engine, not by the task's own `run()` body.
```

with:

```
    Used by the compiled-task runner: identity for a `CompiledTask` is owned by
    the engine, never by the task's own `run()` body. A task that wants to
    report steps it performs itself uses `subtask`, which nests under this one.
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_reporting.py -q`
Expected: PASS, 10 tests — the five existing ones plus these five.

- [x] **Step 5: Commit**

```bash
git add src/sonata_engine/workflow/reporting.py tests/workflow/test_reporting.py
git commit -m "Let a task report the steps inside its own run()"
```

---

### Task 2: Export it, and prove the compiled topology is unchanged

**Files:**
- Modify: `src/sonata_engine/workflow/__init__.py`
- Modify: `src/sonata_engine/__init__.py`
- Test: `tests/core/test_workflow.py`

**Interfaces:**
- Consumes: `subtask` from Task 1.
- Produces: `from sonata_engine import subtask` works.

- [x] **Step 1: Write the failing test**

Add to `tests/core/test_workflow.py`. That file already imports `Task`, `TaskInputs`, `TaskOutcome`, `Workflow` and `bind_workflow_sink`, and already defines `_FakeSink` (around line 133), so the only import to add is `subtask`.

```python
def test_subtasks_do_not_become_compiled_units() -> None:
    """The whole point: a step made of many stays one unit, so ordinals and
    selection are unaffected by what a task does inside its own run()."""

    class Composite(Task[None]):
        title = "Build images"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            for name in ("cp", "fn"):
                with subtask(task_id=f"build-images/{name}", title=name):
                    pass
            return TaskOutcome()

    sink = _FakeSink()
    workflow = Workflow(workflow_id="w")
    workflow.add(Composite())

    compiled = workflow.compile()
    with bind_workflow_sink(sink):
        workflow.run()

    assert [task.task_id for task in compiled.tasks] == ["001.build-images"]
    assert [event.task_id for event in sink.events if event.kind == "task.started"] == [
        "001.build-images",
        "build-images/cp",
        "build-images/fn",
    ]
```

Add one import line to that file:

```python
from sonata_engine import subtask
```

Importing it from the package root rather than from `sonata_engine.workflow.reporting` is deliberate: it is what makes this test fail until Task 2's export exists.

- [x] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/core/test_workflow.py -q`
Expected: FAIL with `ImportError: cannot import name 'subtask' from 'sonata_engine'`.

- [x] **Step 3: Export it**

In `src/sonata_engine/workflow/__init__.py`, add the import and the `__all__` entry:

```python
from sonata_engine.workflow.events import WorkflowContext, WorkflowEvent, WorkflowSink
from sonata_engine.workflow.models import TaskDefinition, TaskRun, WorkflowRun, WorkflowState
from sonata_engine.workflow.reporting import subtask

__all__ = [
    "WorkflowContext",
    "WorkflowEvent",
    "WorkflowSink",
    "TaskDefinition",
    "TaskRun",
    "WorkflowRun",
    "WorkflowState",
    "subtask",
]
```

In `src/sonata_engine/__init__.py`, add `subtask` to the existing `from sonata_engine.workflow import (...)` block and to `__all__`, keeping both in the order those lists already use.

- [x] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/core/test_workflow.py -q`
Expected: PASS.

- [x] **Step 5: Run the whole suite and the gates**

```bash
uv run pytest -q
uv run ruff check src tests
uv run basedpyright
```
Expected: all green, coverage gate satisfied. `tests/test_package_boundaries.py` must still pass — `subtask` adds no import outside `sonata_engine`.

- [x] **Step 6: Commit**

```bash
git add src/sonata_engine/__init__.py src/sonata_engine/workflow/__init__.py tests/core/test_workflow.py
git commit -m "Export subtask and pin the compiled topology it must not change"
```

---

### Task 3: Show it in the README's task section

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the public `subtask` from Task 2. Produces nothing other tasks use.

- [x] **Step 1: Add the section**

README.md's top-level sections are `## v2 workflow API`, `## Resources`, `## Selecting a slice`, `## Journal and resume`, `## Development`. Insert the following between `## Resources` and `## Selecting a slice` — after resources, because the acquire of one is the most common place to want it; before slicing, because the paragraph explains what selection does and does not see. It is a `##` section of its own, not a subsection of Resources: it is unrelated to resource acquisition.

```markdown
## Reporting steps inside a task

A task that does several things can report them without becoming several
units. `subtask` emits the same events a compiled unit does, nested under
whichever task is running:

```python
from sonata_engine import Task, TaskInputs, TaskOutcome, subtask

class BuildImages(Task[None]):
    title = "Build images"

    def __init__(self, slug: str, images: tuple[str, ...]) -> None:
        self._slug = slug
        self._images = images

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        for image in self._images:
            with subtask(task_id=f"{self._slug}/{image}", title=f"Build {image}"):
                ...  # build it
        return TaskOutcome()
```

The step stays one compiled unit: one ordinal, one entry in the journal, one
thing `Selection` can name. Subtasks exist in the event stream only, so a
consumer's UI can show progress through a long step, and a resumed run restarts
that step from its beginning.

Pick `task_id` yourself and keep it unique within the run — a consumer keys
child phases by it, so a repeat merges two steps into one. Do not imitate the
compiler's `NNN.slug`: those ids are the engine's, and a task is not told its
own. `slug` is a constructor argument, not a hardcoded literal, because two
instances of the same task class (two `BuildImages` in one workflow) need
something to tell their subtask ids apart.

Open subtasks sequentially, on the thread running the task. The parent is
resolved through a context shared as a fallback for worker threads (which
start with none of their own); subtasks opened concurrently from worker
threads — or one left open past its `with` block while another opens — nest
under each other instead of under the unit, silently.
```

- [x] **Step 2: Commit**

```bash
git add README.md
git commit -m "Document subtask reporting"
```

---

## Self-review notes

- **Spec coverage.** Design section → Tasks 1 and 2. "What does not change" → the compiled-topology test in Task 2. Testing section → all four engine cases are in Task 1 except topology invariance, which needs a real workflow and so sits in Task 2: both halves of that bullet, the plain unit-list comparison and `Selection(only=<enclosing step>)` keeping the step whole, are asserted there. The consumer-side TUI test named in the spec is downstream work in nanolab, not part of this plan.
- **Deliberately not here.** Nothing teaches the journal about subtasks, and nothing lets `Selection` name one; both are stated non-goals. Workflow-as-a-task is untouched.
- **Risk.** The only edits to existing code are one docstring and one `add_note` message; `subtask` itself adds no behaviour, it delegates to the code the runner already uses. Everything else is additive, which is why the topology test in Task 2 is the load-bearing one: it fails loudly if `subtask` ever starts affecting compilation.
- **Left open deliberately: id collisions — wrong layer, not unhit.** `task_id` is unique within a run only because the caller made it so. A task cannot qualify its ids with its own compiled ordinal — not knowing it is the point — so two instances of the same task class in one workflow emit the same subtask ids. That is not an event-stream defect: the engine gives the two `task.started` events distinct `parent_task_id`s (`001.build-images` vs `002.build-images-again`), so nothing is lost on the wire. The merge is purely a property of a downstream consumer keying its phase map on `task_id` alone (`_phase_by_task_id`). The engine could still close it by prefixing `task_id` with the resolved parent inside `subtask`, but that changes the id shape every consumer already renders and logs, to fix something the consumer fixes by keying on `(parent_task_id, task_id)` instead. Deferral stands; revisit only if keying on the pair turns out to be insufficient, not merely unhit.
