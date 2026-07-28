# Sonata

Sonata is a small, product-independent Python workflow engine. Product tasks,
remote execution, infrastructure providers, and release policy belong in downstream
packages such as nanoFaaS.

- Distribution: `sonata-engine`
- Import package: `sonata_engine`
- Runtime dependencies: none
- Python: 3.12+

## Breaking change in 0.2.0

`0.2.0` changes two call signatures with no compatibility shim. Pre-existing code
must be migrated before upgrading:

- `Task.run(self)` -> `Task.run(self, inputs: TaskInputs)`. Every concrete task's
  `run` now takes the workflow's `TaskInputs` as its one argument.
- `Resource(acquire=lambda: ..., release=lambda: ...)` ->
  `Resource(acquire=lambda inputs: ..., release=lambda inputs, value: ...)`. `acquire`
  now takes `TaskInputs` and returns the resource's runtime value; `release` now takes
  `TaskInputs` and that same value.

Old-shape code raises `TypeError` at call time (e.g. `run() takes 1 positional
argument but 2 were given`) rather than silently misbehaving, so the break is loud.
This is what makes `TaskInputs`/`Resource` dependencies (below) possible at all.

## v2 workflow API

`Workflow.run()` is the only execution entry point. Tasks have no declared IDs:
compilation assigns one deterministic ordinal/slug ID and the engine uses it for
results, journal records, and events.

```python
from sonata_engine import Task, TaskInputs, TaskOutcome, Workflow


class Build(Task[str]):
    title = "Build image"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        return TaskOutcome(value="registry.example/image:v1")


workflow = Workflow(workflow_id="release")
workflow.add(Build())
result = workflow.run()

assert result.by_id("001.build-image").outcome == TaskOutcome(
    value="registry.example/image:v1"
)
```

Every concrete task subclasses `Task[T]` and returns `TaskOutcome[T]`. `TaskInputs`
is the task's capability-limited access to declared resource values. It is distinct
from `WorkflowContext`, which carries flow and task identifiers for reporting and
event correlation; a task receives `TaskInputs`, not `WorkflowContext`. A
`ReusableTask` may be skipped only when it returned non-empty evidence and every
evidence item has a successful verifier. It must also expose a deterministic
`reuse_key` that changes whenever semantic inputs change; this key participates in
the workflow fingerprint. Reusable tasks cannot return a runtime value.

## Resources

Pass resources through `requires`. A `Resource[T]` acquires a runtime value of type
`T` and receives that same typed value when it is released. The compiler inserts
acquire/release units around consumers, and cleanup runs in reverse acquisition
order after success or failure.

```python
from sonata_engine import Resource, TaskInputs


def start_builder(inputs: TaskInputs) -> Builder:
    return Builder()


def stop_builder(inputs: TaskInputs, builder: Builder) -> None:
    builder.stop()

builder: Resource[Builder] = Resource(
    title="Acquire builder",
    acquire=start_builder,
    release=stop_builder,
    acquire_idempotent=True,
)
workflow.add(Build(), requires=(builder,))
```

Within `Build.run`, retrieve the declared value with `inputs.resource(builder)`.
Resources may themselves declare `requires=(other_resource,)`; those dependencies
are acquired first, remain available to their lifecycle callbacks, and are released
after their dependents. A consumer declares only the resources it uses directly.
Dependency cycles fail compilation with `ResourceDependencyCycleError`.

With `Workflow(keep_infrastructure=True)`, every `infrastructure=True` resource and
all of its transitive dependencies are retained. This prevents the engine from
keeping a deployment while releasing the VM or cluster it still depends on.

`acquire_idempotent=False` is the safe default: a failed or interrupted acquire is
ambiguous and resume refuses to retry it automatically.

## Reporting steps inside a task

A task that does several things can report them without becoming several
units. `subtask` emits the same events a compiled unit does, nested under
whichever task is running:

```python
from sonata_engine import Task, TaskInputs, TaskOutcome, subtask

class PublishImages(Task[str]):
    title = "Publish images"

    def __init__(self, slug: str, images: tuple[str, ...]) -> None:
        self._slug = slug
        self._images = images

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        for image in self._images:
            with subtask(task_id=f"{self._slug}/build/{image}", title=f"Build {image}"):
                ...  # build it

        with subtask(task_id=f"{self._slug}/scan", title="Scan for vulnerabilities"):
            ...  # scan everything built above

        with subtask(task_id=f"{self._slug}/push", title="Push the tags"):
            digest = ...  # push, and keep what the registry answered

        return TaskOutcome(value=digest)
```

Subtasks need not come from a loop and need not resemble each other — those are
three different kinds of step, and the last one produces the value the whole
task returns. Added as `PublishImages("publish-images", ("control-plane",
"function-runtime"))`, it emits:

```
001.publish-images
├── publish-images/build/control-plane
├── publish-images/build/function-runtime
├── publish-images/scan
└── publish-images/push
```

The step stays one compiled unit: one ordinal, one entry in the journal, one
thing `Selection` can name. If the scan fails the whole unit fails — sharing
one fate is what makes these one unit rather than four. Subtasks exist in the
event stream only, so a consumer's UI can show progress through a long step,
and a resumed run restarts that step from its beginning.

Pick `task_id` yourself and keep it unique within the run — a consumer keys
child phases by it, so a repeat merges two steps into one. Do not imitate the
compiler's `NNN.slug`: those ids are the engine's, and a task is not told its
own. `slug` is a constructor argument, not a hardcoded literal, because two
instances of the same task class (two `PublishImages` in one workflow) need
something to tell their subtask ids apart.

The snippet above is a sketch. For a version that runs — with the sink the
events need somewhere to go, since `subtask` is a silent no-op without one —
see `examples/demo_workflow.py`:

```
uv run python examples/demo_workflow.py
```

Open subtasks sequentially, on the thread running the task. The parent is
resolved through a context shared as a fallback for worker threads (which
start with none of their own); subtasks opened concurrently from worker
threads — or one left open past its `with` block while another opens — nest
under each other instead of under the unit, silently.

## Selecting a slice

`Selection` narrows a run to some of its consumer tasks, addressed by title slug.

```python
from sonata_engine import Selection

workflow.run(select=Selection(only="build-image"))
workflow.run(select=Selection(start="build-image", until="publish-manifest"))
```

Resources are not selectable: the compiler re-splices acquire and release around
whichever consumers survive, retaining every transitive resource dependency needed
by those consumers, so a slice keeps its setup and cleanup. Ordinals renumber over
the survivors, which makes a sliced run a different topology — `resume` across one
fails closed.

## Journal and resume

```python
from pathlib import Path
from sonata_engine import JournalConfig

result = workflow.run(
    journal=JournalConfig(Path("run/journal.jsonl")),
    resume=True,
    verifiers={"oci-image": verify_oci_image},
)
```

The optional schema-v2 JSON Lines journal is created from the full compiled topology.
Each task starts with attempt `0`, status `pending`; later attempts append lifecycle
records. Every record carries a deterministic workflow fingerprint, and resume fails
if the ordered task topology or task type changed. A torn final line is removed before
continuing, while any complete malformed record raises `CorruptJournalError`.

**Upgrading Sonata invalidates existing journals.** The fingerprint is derived from
the compiled topology, which includes the engine's own internal shape (for example,
adding resource-dependency edges in `0.2.0` changed the fingerprint of every
workflow, even ones that declare no resources). With `resume=True` a fingerprint
mismatch raises `WorkflowTopologyMismatchError` -- loud and correct. Without
`resume` (a plain `journal=` run), old records for a different fingerprint are
simply ignored and a new topology is appended to the *same file*; this now emits a
`UserWarning` (Sonata adds no logging dependency) but the run itself proceeds and
does not fail. Start a fresh journal file after upgrading if you don't want mixed
topologies accumulating in one file.

Runtime values (including `TaskOutcome.value` and acquired resource values) are
in-process only: they are not journaled and are not reconstructed by resume. Make
resumed work depend on durable, verifier-backed evidence rather than a prior runtime
value. Resource acquire callbacks run again on resume, yielding fresh in-process
values for the resumed run.

Sonata only includes the generic `file-digest` verifier. Domain evidence such as OCI
artifacts must be verified by an injected downstream verifier.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check --no-cache .
uv run basedpyright
```

The v2 design and nanoFaaS migration sequence are documented in
[`docs/plans/2026-07-24-release-on-workflow-engine.md`](docs/plans/2026-07-24-release-on-workflow-engine.md).
