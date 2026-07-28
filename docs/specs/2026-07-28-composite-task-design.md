# Composite tasks with data between steps — design

**Status:** proposed
**Baseline:** `d53a7a9`, main after subtask reporting landed (v0.3.0)
**Raised by:** the consumer of `subtask`, immediately after it merged, 2026-07-28

## The problem

`subtask` gave a task a way to report the steps it performs. It did not give it a
way to *be* those steps. Today a composite is written by hand:

```python
class PublishImages(Task[str]):
    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        for image in self._images:
            with subtask(task_id=f"{self._slug}/build/{image}", title=f"Build {image}"):
                ...
```

Three complaints, all correct, and all with one root:

1. **The author writes `run()` every time.** A task assembled from steps should be
   constructed, not subclassed. It should be closed to modification.
2. **The author must know rules that exist only because of the implementation.**
   Open subtasks sequentially on your own thread; keep ids unique within the run;
   do not imitate the compiler's `NNN.slug`. None of these are about the work.
3. **A resumed composite redoes everything.** Five image builds where the fifth
   failed rebuild all five. `subtask`'s spec named this cost and deferred it;
   this spec pays it off.

The root is that **the loop belongs to the task author when it should belong to
the engine**. Every one of the three follows from that, and so does the fix.

## What already exists

Measured against `d53a7a9`, not recalled:

| piece | state |
|-------|-------|
| `Task` (`core/task.py`) | a tiny ABC: `title`, `idempotent`, `run(inputs) -> TaskOutcome[T]`. A step needs nothing more, so **a step is already a Task**. |
| `_slugify` (`core/workflow.py:49`) | the function the compiler uses to build `NNN.slug` from a title. Reusable for step ids. |
| `TaskInputs` (`core/inputs.py`) | "the resource values a task is permitted to observe". Frozen, `slots=True`. Already the one channel through which a task observes what it did not create. |
| `Resource` | today the **only** unit-to-unit data channel. A plain task's `TaskOutcome.value` reaches no other task — it reaches only `WorkflowResult`. |
| `decide_resume` (`journal.py:92`) | run/skip policy: a done task is skipped only if it is `reusable` **and** every evidence entry verifies. Interrupted non-idempotent tasks raise. |
| journal record (`journal.py:326`) | `task_id`, `attempt`, `status`, timestamps, `evidence`. **Never a value.** |
| `core/workflow.py:278` | a `ReusableTask` returning a runtime value is an `InvalidTaskOutcomeError`. |
| `CompiledWorkflow.fingerprint` (`core/compiled.py:63`) | per unit: `task_id`, `kind`, class path, `reuse_key`, acquire ids of required resources. |
| `subtask` (`workflow/reporting.py`) | reports a step, nested under the running task. Executes nothing. |

## Design

### One construct, always sequential

```python
Steps(title="Deploy the chart", steps=(HelmInstall(chart), WaitRollout(), ResolveEndpoint()))
```

`Steps` is a concrete `Task` in `core/steps.py`, exported from `sonata_engine`. It
imports `subtask` from `workflow/reporting`; `core/workflow.py:27` already imports
from there, so the direction is established.

**The compiler does not know it exists.** It sees one task like any other, which
is what keeps nested-workflow identity out of this design.

The engine runs the steps in order, one at a time, on the thread already
running the task. The threading hazard `subtask` had to document —
concurrently opened subtasks nesting under each other — cannot arise here,
because the loop belongs to the engine and it opens one subtask at a time.
The rule disappears instead of being taught.

The independent case uses the same construct and simply never asks for the
upstream value. One thing to learn.

### Data between steps

`TaskInputs` gains a declared field for the upstream value. It must be declared:
`slots=True` makes attaching one at runtime impossible, which is the right
constraint to be stopped by.

```python
def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
    release = inputs.upstream()
```

For each step the composite builds a **copy** of `TaskInputs` carrying that
step's upstream, via `dataclasses.replace`. A copy, not a mutation: the value
arrives as a function argument, never as ambient state someone reads. That
distinction is the whole difference between this and the shared-global fallback
in `bind_workflow_context`.

A step observes the resources the **composite** declared, identical for every
step. A step cannot declare resources of its own — see Limitations.

`Steps` returns the last step's value. Python cannot express "the type of the
last element of a tuple", so `Steps` is `Task[Any]`; a caller wanting a tighter
type wraps it.

### Identity: the engine names the steps

The composite never sees a compiler-assigned id, but the **runner** does, and the
runner is what constructs the step-scoped handle the composite works through. So
the handle does the naming: the composite offers a step, the handle produces both
the subtask id and the journal id, each prefixed with the compiled unit id.

```
001.deploy/install-chart
001.deploy/wait-for-rollout
001.deploy/resolve-endpoint
```

Three consequences, all free:

- Ids are **unique within the run by construction**. The uniqueness rule `subtask`
  imposes on its callers disappears for composites.
- Two instances of the same composite class in one workflow no longer collide.
  That was recorded as deliberately open in `subtask`'s plan; this closes it for
  composites without changing `subtask`.
- `Steps` takes **no slug argument**. The caller supplies titles, nothing else.

The step segment comes from `_slugify(step.title)`, the compiler's own function.

### The journal, per step

`TaskInputs` carries the handle, alongside the upstream value. The division
of labour is fixed: the **handle** owns everything needing the compiled unit
id or the journal — producing step ids, deciding, recording. **`Steps`** owns
the loop and threads the upstream value, and opens each `subtask` with the id
the handle gave it. Neither reaches into the other's half.

For each step:
ask `decide`; on `skip`, record the skip and move on; otherwise record `started`,
run it inside a `subtask`, then record `passed` with its evidence or `failed`.

The policy is **`decide_resume` unchanged**, not a second implementation of it:
reusable with verifying evidence skips, everything else reruns, and an
interrupted non-idempotent step raises `AmbiguousTaskStateError` exactly as a
unit does.

`CompiledWorkflow.fingerprint` extends to cover a composite's step list — each
step's class path and `reuse_key`, the same fields it already collects per unit.
Without that, editing a composite's steps would silently reuse stale step
records, which is the class of silent failure this design exists to remove.

### Why resume and data flow do not collide

They appear to: if step 3 is skipped, where does step 4's upstream come from?
The question does not arise, because **Sonata already forbids the combination**.
`core/workflow.py:278` rejects a `ReusableTask` that returns a runtime value, and
the README states that runtime values are never journalled or reconstructed —
resource acquire callbacks rerun to produce fresh ones.

So the invariant is already written: **skippable if and only if it produces no
value.** The two cases partition themselves:

- **Value-producing steps** (install a release, then read the address it exposes)
  rerun on resume. Correct: a release name is in-process state and must be fresh.
- **Reusable, evidence-producing steps** (five image builds, evidence being the
  image digest, verified by an injected downstream verifier as the README already
  prescribes for OCI artifacts) skip on resume. The fifth fails, you resume, the
  first four skip.

Nothing downstream can need a skipped step's value, because a skippable step is
forbidden from having one.

## Errors and edge cases

| case | behaviour |
|------|-----------|
| duplicate step titles | `ValueError` at construction. The title is both the id segment and what the UI shows; duplicates are ambiguous either way. |
| `steps=()` | `ValueError` at construction. |
| step returns a non-`TaskOutcome` | `InvalidTaskOutcomeError`, the error the runner already raises for units. |
| `upstream()` on the first step | raises `NoUpstreamValueError`, new in `errors.py` beside `ResourceUnavailableError`, which has the same shape. Not `None`: `None` is a legitimate step value, and the two cases must stay distinguishable. |
| a step raises | its subtask emits `task.failed`, the exception propagates, the unit fails. Existing `subtask` behaviour, unchanged. |
| a reusable step returns a value | already an `InvalidTaskOutcomeError`. Inherited, not restated. |
| a composite among the steps | works, ids nest. Pinned by a test rather than left to be discovered. |

## Limitations, stated rather than hidden

- **A step declares no resources of its own.** It observes what the composite
  declared. Work needing its own resource is not a step — it is a unit, and
  belongs in the workflow.
- **`Selection` still names only the composite**, never one of its steps. Steps
  share one fate; selecting a slice cannot split them.
- **Steps are sequential.** There is no parallel composite in this design.

## Alternatives rejected

**A compiler-aware composite** that expands steps into addressable sub-units.
It would give `Selection` and journal addressing per step, but the compiler
numbers `001.`, `002.` over one flat list, so it must first answer what a nested
unit's identity is, how selection addresses it, and what the journal records —
the nested-workflow design `subtask`'s spec parked. None of those answers are
needed to solve the problem in hand.

**Upstream carried ambiently in a contextvar**, leaving `TaskInputs` untouched.
It is the same shape of defect — ambient state read at the wrong moment — that
`bind_workflow_context`'s shared global produced. Named here because it is the
shortcut that suggests itself.

**A new `Step` type with `run(inputs, upstream)`.** An explicit signature, but
existing `Task` implementations would need an adapter and the engine would carry
two near-identical hierarchies. Delivering upstream through `TaskInputs` keeps
`Task`'s signature untouched and lets any already-written task serve as a step.

**Steps as bare callables.** Lightest, but it discards `title`, `idempotent` and
`reuse_key`, which the journal design depends on.

**A separate parallel group construct.** Rejected with the pipeline choice: it
needs its own failure semantics while some steps are still running, and the
motivating workload is served sequentially today.

## Testing

- a value flows through the pipeline, including a step that only forwards it;
- a composite compiles to exactly one unit, and `Selection` naming it keeps it
  whole;
- an already-written `Task` serves as a step unmodified — the central promise;
- `upstream()` on the first step raises;
- duplicate titles and an empty step list raise at construction;
- a step returning a non-`TaskOutcome` raises `InvalidTaskOutcomeError`;
- **on resume, a reusable step with verifying evidence skips while the steps
  after it run** — the point of the journal work;
- an interrupted non-idempotent step raises `AmbiguousTaskStateError`;
- changing a composite's step list invalidates resume through the fingerprint;
- a composite nested inside a composite nests its ids.

## Consequences downstream

In nanolab, `validate` builds and pushes images as one step whose progress is
invisible, and `cli` installs a Helm release and then resolves the address it
exposes. Both become `Steps`. The image builds are the case that motivated the
journal work: today a failure in the last build costs every earlier build again.
