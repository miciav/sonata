# Sonata subtask reporting — design

**Status:** proposed
**Baseline:** `527d042a`, the commit nanolab currently pins
**Raised by:** a consumer (nanolab) building N container images as one workflow
step, 2026-07-27

## The problem

Two shapes keep coming up in nanoFaaS workflows:

1. **One step made of many.** Building five images, or installing a Helm release
   and then reading the address it exposes. They belong together because they
   share one fate: if the third build fails, the step failed.
2. **A workflow used as a step inside another workflow.**

Shape 1 is already expressible — `compensated_resource`'s acquire runs a
sequence, and `function_resource` and `_helm_release_with_endpoint` both use it.
What is missing is not the ability to *run* subtasks, it is the ability to
*report* them: the step appears in the plan as a single line, and during a run
the TUI shows nothing between "started" and "passed". For five image builds of a
few minutes each, that is the difference between a progress display and a mute
block.

Shape 2 is not expressible at all and is out of scope here; see Non-goals.

## What already exists

The reporting path is complete except for one link. Measured against `527d042a` and the
TUI of the consumer that surfaced this:

| layer | state |
|-------|-------|
| consumer TUI (`nanolab/tui/event_aggregator.py`) | real hierarchy: `TuiPhaseSnapshot.children`, `parent_task_id`, `_ensure_child_phase` builds and reuses child phases |
| `src/sonata_engine/workflow/events.py` | `WorkflowEvent.parent_task_id` and `WorkflowContext.parent_task_id` both exist |
| `src/sonata_engine/workflow/reporting.py` | `_child_context` resolves an absent parent from the active context: `resolved_parent = active.task_id or active.parent_task_id` |
| `src/sonata_engine/workflow/reporting.py` | `_task_lifecycle` binds that context for the duration of the task: `with bind_workflow_context(child)` |
| `src/sonata_engine/core/workflow.py` | the runner wraps every compiled unit in `_task_lifecycle` |

Put together: **while a task's `run()` executes, the active context already
carries that task's id, and any lifecycle event emitted inside it would be
nested under it automatically.** Nothing needs to be threaded through by hand.

The missing link is that a task has no supported way to emit those events.
`_task_lifecycle`, `_child_context`, `_emit` and `build_task_event` are all
private, and `_task_lifecycle`'s docstring states the restriction explicitly:

> Used exclusively by the compiled-task runner, so lifecycle identity for a
> `CompiledTask` is owned by the engine, not by the task's own `run()` body.

That sentence is the whole design decision, and it is the one this spec revises.

## Design

Expose the mechanism that already exists, and nothing more.

### The engine change

Add one public context manager, exported from `sonata_engine`:

```python
@contextmanager
def subtask(*, task_id: str, title: str = "") -> Generator[None, None, None]:
    """Report one step performed inside a task's own run().

    Emits task.started/passed/failed like a compiled unit, but nested under
    whichever task is currently running. Subtasks are a reporting concern only:
    they get no compiler-assigned identity, take no part in selection, and are
    not journalled — the enclosing unit remains the unit of work.
    """
```

Implementation is `_task_lifecycle` with its restriction lifted: same emit,
same `_child_context`, whose existing fallback supplies the parent.

`task_id` is the caller's to choose and must be unique within the run; the
compiler owns `NNN.slug` identities and they must never be imitated. A task is
not told the ordinal the compiler gave it, so the id has to be built from what
the task itself knows — its own name extended by the step reads well, e.g.
`build-images/control-plane`. The aggregator keys children by `task_id`
(`_phase_by_task_id`), so a repeated id would merge two steps into one phase;
two instances of the same composite in one workflow therefore need something
that tells them apart.

Nesting is whatever the call stack produces: a subtask that itself opens a
subtask nests two deep, because each `subtask` binds its own context. The TUI
renders arbitrary depth; no depth limit is imposed by the engine.

### What a composite task then looks like

No new base class. `Task` is already an ABC anyone can implement, so a composite
is ordinary object composition — which is exactly how `workflow-tasks` did it
(`RunK6WithReplicaWatch` holds a `Runnable` and calls `.run()` on it):

```python
class DockerBuildImagesTask(Task[tuple[TaskResult, ...]]):
    """Build several images as one step of the workflow."""

    def __init__(self, *, title: str, slug: str, builds: tuple[DockerBuildTask, ...]) -> None:
        self.title = title
        self._slug = slug
        self._builds = builds

    def run(self, inputs: TaskInputs) -> TaskOutcome[tuple[TaskResult, ...]]:
        results = []
        for build in self._builds:
            with subtask(task_id=f"{self._slug}/{build.image}", title=build.title):
                results.append(build.run(inputs).value)
        return TaskOutcome(value=tuple(results))
```

That class belongs to the consumer, not here: the engine gains a reporting
primitive, and whoever builds images writes the task that uses it. Sonata stays
product-independent.

### What does not change

- **Selection.** `Selection(only=…)` filters compiled units before compiling.
  Subtasks are not units, so `--only` still names the enclosing step. Selecting
  a step keeps all of its subtasks, which is the intent — they share one fate.
- **Ordinals.** The compiler numbers only compiled units. Adding subtasks to a
  step does not renumber anything, so existing task ids stay stable.
- **The journal.** Subtasks are not recorded and cannot be resumed into. A
  resumed step re-runs from its start. This matters for the image case — five
  builds re-run if the fifth failed — and is the deliberate cost of keeping the
  unit of work whole. Revisit only if a real workflow makes it painful.
- **`TaskInputs`.** Unchanged: subtasks receive the enclosing task's inputs, so
  they can read exactly the resources their parent declared.

## Alternatives rejected

**Thread `parent_task_id` through explicitly.** Every composite would have to
learn its own compiled id, which the compiler assigns and the task deliberately
does not know. The context fallback already solves it.

**Make the compiler emit subtasks as units with dotted ids.** They would then
be selectable and journalled, but the compiler would have to look inside a
task's body to find them — it cannot, and should not: the body may decide at run
time how many builds there are.

**A `CompositeTask` base class in the engine.** One implementation, no
behaviour beyond looping, and it would push the engine into knowing what a
sequence-of-tasks means. `Task` plus `subtask` covers it.

## Non-goals

**A workflow used as a task.** `Workflow` exposes `compile()`/`run()`, not
`run(inputs) -> TaskOutcome`, and adapting it is not the hard part: the compiler
assigns `001.`, `002.` over one flat list, so a nested workflow has no place in
that numbering. Making it fit means deciding what a nested unit's identity is,
how selection addresses it, and what the journal records — a larger design that
this one does not prejudge. Subtask reporting is useful on its own and does not
block it.

**Progress within a subtask** (percentages, byte counts). Out of scope; the
event stream carries `detail` if a task wants to say more.

## Testing

In the engine:

- a task that opens a subtask emits `task.started`/`task.passed` for it, with
  `parent_task_id` equal to the enclosing unit's compiler-assigned id — proving
  the fallback, not a hand-threaded value;
- a failing subtask emits `task.failed` and the exception still propagates to
  fail the enclosing unit;
- two levels of nesting produce two distinct parents;
- a workflow with subtasks compiles to the same unit list as one without, and
  `Selection(only=<enclosing step>)` keeps it whole.

Downstream, in the consumer: its TUI aggregator already has child-phase tests;
one more should feed it an event carrying `parent_task_id` produced by a real
Sonata run, rather than a hand-built event, and assert the child lands under the
right phase.

## Consequences downstream

In nanolab, `validate` and `cli` each have steps that would become composites
once this lands: the image builds, and the Helm-install-then-resolve pair whose
second half is currently invisible in the plan. Neither is urgent — this spec
exists because the image-build case is about to make the gap visible, not
because anything is broken.
