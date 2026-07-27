# Subtask Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a task report the steps it performs inside its own `run()`, so a step made of many appears as many in the event stream without becoming many compiled units.

**Architecture:** `_task_lifecycle` already binds a context carrying the running task's id, and `_child_context` already resolves an absent parent from that context. Nesting therefore needs no new plumbing — only a public entry point. This adds one context manager, `subtask`, which is `_task_lifecycle` without its "runner only" restriction, and exports it.

**Tech Stack:** Python 3.12+, standard library only, pytest, Ruff, Basedpyright.

**Spec:** `docs/specs/2026-07-27-subtask-reporting-design.md`

## Global Constraints

- Sonata takes no runtime dependencies and never imports from a downstream product. `tests/test_package_boundaries.py` enforces this.
- The compiler owns compiled-unit identity. `subtask` must never assign, derive, or renumber a compiled `NNN.slug` id.
- Selection, ordinals, the journal and `TaskInputs` are untouched by this change. A workflow whose tasks open subtasks must compile to exactly the same unit list as one whose tasks do not.
- Event kinds stay the three that exist: `task.started`, `task.passed`, `task.failed`.
- Coverage gate and lint settings in `pyproject.toml` apply as they stand; do not relax them.

---

### Task 1: The `subtask` context manager

**Files:**
- Modify: `src/sonata_engine/workflow/reporting.py`
- Test: `tests/workflow/test_reporting.py`

**Interfaces:**
- Consumes: `_child_context`, `_emit`, `build_task_event`, `bind_workflow_context` — all already in `reporting.py`.
- Produces: `subtask(*, task_id: str, title: str = "") -> AbstractContextManager[None]`, later exported from `sonata_engine`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/workflow/test_reporting.py`. `_FakeSink` and the imports of `bind_workflow_sink` already exist at the top of that file; add `subtask` and `bind_workflow_context` to the existing import lines.

```python
def test_subtask_emits_started_and_passed() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with subtask(task_id="003.build/cp", title="Build control plane"):
            pass

    assert [event.kind for event in sink.events] == ["task.started", "task.passed"]
    assert sink.events[0].task_id == "003.build/cp"
    assert sink.events[0].title == "Build control plane"


def test_subtask_nests_under_the_task_that_is_running() -> None:
    """The parent is read from the bound context, not passed in: a task does not
    know the id the compiler gave it."""
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with bind_workflow_context(WorkflowContext(task_id="003.build-images")):
            with subtask(task_id="003.build-images/cp", title="cp"):
                pass

    assert {event.parent_task_id for event in sink.events} == {"003.build-images"}


def test_subtasks_nest_to_whatever_depth_the_call_stack_produces() -> None:
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with bind_workflow_context(WorkflowContext(task_id="001.outer")):
            with subtask(task_id="001.outer/mid", title="mid"):
                with subtask(task_id="001.outer/mid/inner", title="inner"):
                    pass

    parents = {event.task_id: event.parent_task_id for event in sink.events}
    assert parents["001.outer/mid"] == "001.outer"
    assert parents["001.outer/mid/inner"] == "001.outer/mid"


def test_a_failing_subtask_reports_and_still_propagates() -> None:
    """Reporting must not swallow the failure: the enclosing unit has to fail."""
    sink = _FakeSink()
    with bind_workflow_sink(sink):
        with pytest.raises(RuntimeError, match="image build failed"):
            with subtask(task_id="003.build/cp", title="cp"):
                raise RuntimeError("image build failed")

    assert [event.kind for event in sink.events] == ["task.started", "task.failed"]
    assert "image build failed" in sink.events[1].detail


def test_subtask_is_a_noop_without_a_sink() -> None:
    with subtask(task_id="003.build/cp", title="cp"):
        pass  # no error, no crash
```

Add the imports this needs to the top of the file:

```python
import pytest

from sonata_engine.workflow.context import bind_workflow_context, bind_workflow_sink
from sonata_engine.workflow.events import WorkflowContext, WorkflowEvent
from sonata_engine.workflow.reporting import status, subtask, workflow_log
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_reporting.py -q`
Expected: FAIL with `ImportError: cannot import name 'subtask'`.

- [ ] **Step 3: Implement it**

In `src/sonata_engine/workflow/reporting.py`, add `subtask` immediately after `_task_lifecycle`. It is the same body; the difference is that it is public and takes no `context` override, because a caller inside a running task must not be able to reparent itself somewhere else.

```python
@contextmanager
def subtask(*, task_id: str, title: str = "") -> Generator[None, None, None]:
    """Report one step performed inside a task's own `run()`.

    Emits the same three events a compiled unit does, nested under whichever
    task is currently running — the parent comes from the bound context, so a
    task never has to know the id the compiler gave it.

    Subtasks are a reporting concern only. They get no compiler-assigned
    identity, take no part in selection, and are not journalled: the enclosing
    unit stays the unit of work, and a resumed step restarts from its beginning.

    `task_id` is the caller's to choose and must be unique within the run;
    `NNN.slug` belongs to the compiler and must not be imitated. A dotted or
    slashed extension of the enclosing id reads well: `003.build-images/cp`.
    """
    child = _child_context(task_id=task_id, parent_task_id=None, context=None)
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
            yield
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
                exc.add_note(f"Failed to emit task.failed for subtask {task_id}: {reporting_error}")
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
```

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_reporting.py -q`
Expected: PASS, all five new tests plus the existing ones.

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_workflow.py`. That file already imports `Task`, `TaskInputs`, `TaskOutcome`, `Workflow` and `bind_workflow_sink`, and already defines `_FakeSink` (around line 133), so the only import to add is `subtask`.

```python
def test_subtasks_do_not_become_compiled_units() -> None:
    """The whole point: a step made of many stays one unit, so ordinals and
    selection are unaffected by what a task does inside its own run()."""

    class Composite(Task[None]):
        title = "Build images"

        def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
            for name in ("cp", "fn"):
                with subtask(task_id=f"001.build-images/{name}", title=name):
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
        "001.build-images/cp",
        "001.build-images/fn",
    ]
```

Add one import line to that file:

```python
from sonata_engine import subtask
```

Importing it from the package root rather than from `sonata_engine.workflow.reporting` is deliberate: it is what makes this test fail until Task 2's export exists.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/core/test_workflow.py -q`
Expected: FAIL with `ImportError: cannot import name 'subtask' from 'sonata_engine'`.

- [ ] **Step 3: Export it**

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

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/core/test_workflow.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite and the gates**

```bash
uv run pytest -q
uv run ruff check src tests
uv run basedpyright
```
Expected: all green, coverage gate satisfied. `tests/test_package_boundaries.py` must still pass — `subtask` adds no import outside `sonata_engine`.

- [ ] **Step 6: Commit**

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

- [ ] **Step 1: Add the section**

README.md's top-level sections are `## v2 workflow API`, `## Resources`, `## Selecting a slice`, `## Journal and resume`, `## Development`. Insert the following between `## Resources` and `## Selecting a slice` — after resources, because the acquire of one is the most common place to want it; before slicing, because the paragraph explains what selection does and does not see.

```markdown
### Reporting steps inside a task

A task that does several things can report them without becoming several
units. `subtask` emits the same events a compiled unit does, nested under
whichever task is running:

```python
from sonata_engine import Task, TaskOutcome, subtask

class BuildImages(Task[None]):
    title = "Build images"

    def __init__(self, images: tuple[str, ...]) -> None:
        self._images = images

    def run(self, inputs):
        for image in self._images:
            with subtask(task_id=f"build-images/{image}", title=f"Build {image}"):
                ...  # build it
        return TaskOutcome()
```

The step stays one compiled unit: one ordinal, one entry in the journal, one
thing `Selection` can name. Subtasks exist in the event stream only, so a
consumer's UI can show progress through a long step, and a resumed run restarts
that step from its beginning.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Document subtask reporting"
```

---

## Self-review notes

- **Spec coverage.** Design section → Tasks 1 and 2. "What does not change" → the compiled-topology test in Task 2. Testing section → all four engine cases are in Task 1 except topology invariance, which needs a real workflow and so sits in Task 2. The consumer-side TUI test named in the spec is downstream work in nanolab, not part of this plan.
- **Deliberately not here.** Nothing teaches the journal about subtasks, and nothing lets `Selection` name one; both are stated non-goals. Workflow-as-a-task is untouched.
- **Risk.** The only behavioural change to existing code is one docstring. Everything else is additive, which is why the topology test in Task 2 is the load-bearing one: it fails loudly if `subtask` ever starts affecting compilation.
