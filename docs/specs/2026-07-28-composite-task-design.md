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

`Steps` is a concrete `Task[Any]` in `core/steps.py`, exported from
`sonata_engine`.

**The compiler does not special-case it.** It sees one task like any other,
which keeps nested-workflow identity out of compilation and selection.

`Steps.idempotent` is always `True`. This says only that the coordinator is safe
to re-enter after an interrupted or failed attempt; it says nothing about the
steps being idempotent. On resume the coordinator re-enters its loop, and each
step's own journal state and `idempotent` flag decide whether that step may run.
Without this, the failed journal record for the enclosing compiled unit would
raise `AmbiguousTaskStateError` before `Steps.run()` could consult any step
record.

The engine runs the steps in order, one at a time, on the thread already running
the task. The threading hazard `subtask` had to document — concurrently opened
subtasks nesting under each other — cannot arise in the loop itself because only
one step lifecycle is open at a time. A step that starts its own worker threads
still owns their lifetime, exactly as an ordinary task does.

The independent case uses the same construct and simply never asks for the
upstream value. One thing to learn.

### Data between steps

`TaskInputs` gains a declared field for the upstream value. It must be declared:
`slots=True` makes attaching one at runtime impossible, which is the right
constraint to be stopped by. The field defaults to a private `_NO_UPSTREAM`
sentinel, so `TaskInputs.empty()` and the existing constructor remain usable.

```python
def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
    release = inputs.upstream()
```

For each step the composite builds a **copy** of `TaskInputs` carrying that
step's upstream, via `dataclasses.replace`. A copy, not a mutation: the value
arrives as a function argument, never as ambient state someone reads. That
distinction is the whole difference between this and the shared-global fallback
in `bind_workflow_context`.

A top-level `Steps` starts with `_NO_UPSTREAM`, so `upstream()` in its first
step raises. A nested `Steps` starts with the upstream value it received from
its parent, so composition is transparent: the first nested step can consume
the value produced immediately before the nested composite.

The loop updates the value from the shared executor's `TaskExecution`: a step
that ran contributes `execution.outcome.value`, including a legitimate `None`;
a skipped step contributes `None`, the only value a skippable `ReusableTask`
may return. The non-empty composite then returns `TaskOutcome(value=upstream)`.

A step observes the resources the **composite** declared, identical for every
step. A step cannot declare resources of its own — see Limitations.

`Steps` returns the last step's value. Python cannot express "the type of the
last element of a tuple", so a caller wanting a tighter type wraps it.

### Identity: the engine names the steps

The composite never sees a compiler-assigned id, but the **runner** does, and the
runner is what constructs the step-scoped handle the composite works through. So
the handle does the naming: the composite offers a step, the handle produces both
the child event id and the journal id, each prefixed with the compiled unit id.

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
Construction rejects an empty normalized slug and duplicate **normalized
slugs**, not merely duplicate title strings: `"Build A"` and `"Build-A"` must
not produce the same journal identity.

### The journal, per step

`TaskInputs` carries a private step-runner handle alongside the upstream value.
The compiled runner creates it with the compiled unit id, journal and `resume`
flag. The handle owns everything needing that state: producing a child id,
scoping a nested handle beneath it, deciding, recording and emitting lifecycle
events. `Steps` owns only the loop and threads the upstream value.

There is one internal execution routine shared by consumer/acquire units and
steps; release units keep their existing cleanup-specific path. The shared
routine performs the existing sequence in one place:

1. only when `resume=True`, ask the journal to decide;
2. on `skip`, record and emit `task.skipped`;
3. otherwise record `started`, run inside `_task_lifecycle`, validate the result,
   then record `passed` with its evidence or `failed`;
4. reject a non-`TaskOutcome` and a non-`None` value from `ReusableTask` with
   `InvalidTaskOutcomeError`.

The existing compiled-unit runner delegates to this routine, and the step handle
delegates to the same routine. The validation and journal error behaviour are
therefore inherited in fact, rather than copied into `Steps`. `subtask` remains
the public reporting-only primitive for hand-written tasks; `Steps` does not use
it.

The policy is **`decide_resume` unchanged**, not a second implementation of it:
reusable with verifying evidence skips, everything else reruns, and an
interrupted non-idempotent step raises `AmbiguousTaskStateError` exactly as a
unit does. With `resume=False`, every step runs even when the configured journal
already contains a verifying reusable record, matching current unit behaviour.

`Task` gains one protected fingerprint-payload method with a default that
preserves existing tasks unchanged. `ReusableTask` contributes its `reuse_key`.
`Steps` contributes an ordered tuple containing each normalized step slug, class
path and fingerprint payload. A nested `Steps` therefore contributes its
children recursively.

`CompiledWorkflow.fingerprint` consumes that generic task payload instead of
checking for `Steps`. It still sees one compiled unit, while changes to step
slugs, order, classes, reuse keys or nested step lists invalidate resume.

### Why resume and data flow do not collide

They appear to: if step 3 is skipped, where does step 4's upstream come from?
The answer is already fixed by `ReusableTask`: its result type is
`TaskOutcome[None]`, and `core/workflow.py:278` rejects any non-`None` runtime
value. A skipped step can therefore reconstruct its only legal upstream value,
`None`, without storing a runtime value in the journal.

The two cases are:

- **Value-producing steps** (install a release, then read the address it exposes)
  rerun on resume. Correct: a release name is in-process state and must be fresh.
- **Reusable, evidence-producing steps** (five image builds, evidence being the
  image digest, verified by an injected downstream verifier as the README already
  prescribes for OCI artifacts) skip on resume and contribute `None` as upstream,
  exactly as they did when they ran. The fifth fails, you resume, the first four
  skip and the fifth retries because the image-build task is also
  `idempotent=True`.

`reusable` and `idempotent` answer different questions and remain independent:
the former permits a verified successful step to skip; the latter permits a
failed or interrupted step to retry.

`_NO_UPSTREAM` is used only when there is no preceding value at all. It is
distinct from `None`, which remains a legitimate and reconstructible step value.

## Errors and edge cases

| case | behaviour |
|------|-----------|
| duplicate normalized step slugs | `ValueError` at construction. Different titles that normalize to the same journal id are ambiguous. |
| a step title normalizes to an empty slug | `ValueError` at construction, matching the compiler's rejection of empty task slugs. |
| `steps=()` | `ValueError` at construction. |
| step returns a non-`TaskOutcome` | the shared executor raises `InvalidTaskOutcomeError`. |
| `upstream()` with no incoming or preceding value | raises `NoUpstreamValueError`, new in `errors.py` beside `ResourceUnavailableError`, which has the same shape. Not `None`: `None` is a legitimate step value. |
| a step raises | its child lifecycle emits `task.failed`, the exception propagates, and the enclosing unit fails. |
| a reusable step returns a non-`None` value | the shared executor raises `InvalidTaskOutcomeError`, exactly as for a compiled unit. |
| a composite among the steps | works: ids and fingerprint payloads nest, and its first step receives the outer upstream. |

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

**A `Steps` special case in `CompiledWorkflow.fingerprint`.** It is smaller for
one release but makes the compiler know the construct and fails again for the
next composite type. A protected payload with a no-op default keeps existing
tasks unchanged and makes recursive identity belong to the task that owns the
children.

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
- `None` flows as a legitimate value, including across a skipped reusable step;
- a composite compiles to exactly one unit, and `Selection` naming it keeps it
  whole;
- an already-written `Task` serves as a step unmodified — the central promise;
- `upstream()` on the first step of a top-level composite raises;
- a nested composite's first step receives the outer upstream;
- duplicate normalized slugs, an empty slug and an empty step list raise at
  construction;
- a step returning a non-`TaskOutcome` raises `InvalidTaskOutcomeError`;
- a reusable step returning a non-`None` value raises `InvalidTaskOutcomeError`;
- **after the enclosing unit has failed, resume re-enters `Steps`, skips reusable
  steps with verifying evidence and retries the failed idempotent step** — the
  point of the journal work;
- `resume=False` runs all steps even when the journal contains reusable records;
- an interrupted non-idempotent step raises `AmbiguousTaskStateError`;
- changing a step slug, order, class, reuse key or nested step list invalidates
  resume through the fingerprint;
- a composite nested inside a composite nests its ids and lifecycle parents.

## Consequences downstream

In nanolab, `validate` builds and pushes images as one step whose progress is
invisible, and `cli` installs a Helm release and then resolves the address it
exposes. Both become `Steps`. The image builds are the case that motivated the
journal work: today a failure in the last build costs every earlier build again.
