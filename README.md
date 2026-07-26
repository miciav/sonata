# Sonata

Sonata is a small, product-independent Python workflow engine. Product tasks,
remote execution, infrastructure providers, and release policy belong in downstream
packages such as nanoFaaS.

- Distribution: `sonata-engine`
- Import package: `sonata_engine`
- Runtime dependencies: none
- Python: 3.12+

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

`acquire_idempotent=False` is the safe default: a failed or interrupted acquire is
ambiguous and resume refuses to retry it automatically.

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
