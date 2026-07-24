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
from sonata_engine import Task, TaskOutcome, Workflow


class Build(Task[str]):
    title = "Build image"

    def run(self) -> TaskOutcome[str]:
        return TaskOutcome(value="registry.example/image:v1")


workflow = Workflow(workflow_id="release")
workflow.add(Build())
result = workflow.run()

assert result.by_id("001.build-image").outcome == TaskOutcome(
    value="registry.example/image:v1"
)
```

Every concrete task subclasses `Task[T]` and returns `TaskOutcome[T]`. A
`ReusableTask` may be skipped only when it returned non-empty evidence and every
evidence item has a successful verifier; reusable tasks cannot return a runtime
value.

## Resources

Pass resources through `requires`. The compiler inserts acquire/release units around
their consumers and cleanup runs in reverse acquisition order after success or
failure.

```python
from sonata_engine import Resource

builder = Resource(
    title="Acquire builder",
    acquire=start_builder,
    release=stop_builder,
    acquire_idempotent=True,
)
workflow.add(Build(), requires=(builder,))
```

`acquire_idempotent=False` is the safe default: a failed or interrupted acquire is
ambiguous and resume refuses to retry it automatically.

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
