# Sonata Engine and nanoFaaS Release Workflow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract the generic workflow engine into the public `miciav/sonata` repository, evolve it to the v2 task/journal contract, and implement nanoFaaS release as an ordinary workflow built from nanoFaaS-specific tasks.

**Architecture:** Sonata owns only task contracts, workflow compilation and execution, generated task IDs, resource lifecycle, optional journal/resume, and generic events. nanoFaaS owns its task library and the release task implementations. `controlplane-tool` imports both dependencies and assembles the release workflow; neither Sonata nor the task library imports `controlplane-tool`.

**Tech Stack:** Python 3.12, uv, pytest, Ruff, Basedpyright, dataclasses, JSON Lines journals.

**Implementation status (2026-07-24):** Sonata tasks 1–7 are implemented and hardened.
The nanoFaaS integration and release scenario in tasks 8–12 remain a separate
downstream change; Sonata contains no release-specific task or policy.

---

## Decisions and invariants

### Repository and dependency boundaries

The final dependency graph is:

```text
controlplane-tool ───────> sonata-engine
       │
       └───────────────> nanofaas-workflow-tasks ───────> sonata-engine
```

- Repository: `https://github.com/miciav/sonata`
- Distribution: `sonata-engine`
- Import package: `sonata_engine`
- Sonata must not import any `nanofaas`, `controlplane_tool`, VM-provider, Ansible,
  load-test, release, or remote-execution module.
- Remote execution, cloud retry policy, credentials, VM providers, and release tasks
  remain in nanoFaaS.
- Release workflow assembly belongs to `controlplane_tool`.
- The initial extraction copies the current generic `core/` and `workflow/` code and
  tests. It is a provenance-preserving bootstrap, not the v2 API.

### v2 only

- Sonata v2 has no compatibility layer for the current `workflow_tasks` API.
- Journal schema v2 does not read, migrate, or silently accept schema v1.
- Existing nanoFaaS workflows migrate in the same cut; no adapter package remains.
- Runtime exposes one identity, `task_id`. There is no `node_id`, `operation_id`,
  task-declared ID, or ID alias.

### Task contract

Every task explicitly subclasses the generic ABC and returns `TaskOutcome[T]`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str
    reference: str
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class TaskOutcome(Generic[T]):
    value: T | None = None
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)


class Task(Generic[T], ABC):
    title: str
    idempotent: bool = False

    @abstractmethod
    def run(self) -> TaskOutcome[T]:
        raise NotImplementedError


class ReusableTask(Task[None], ABC):
    reusable: bool = True

    @property
    @abstractmethod
    def reuse_key(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def run(self) -> TaskOutcome[None]:
        raise NotImplementedError
```

- `TaskOutcome.value` is an in-process data channel and is never serialized by the
  journal.
- Only `ReusableTask` can be skipped from verified journal evidence.
- A reusable task has no mutable result channel and returns `TaskOutcome[None]`.
- Every reusable task supplies a deterministic `reuse_key`; it changes whenever
  semantic configuration affecting the reusable output changes.
- `idempotent` permits safe retry; it does not imply journal reuse.
- Reconciliation tasks are normally idempotent and non-reusable.
- The engine rejects a non-`TaskOutcome` runtime result even if static checks were
  bypassed.
- Basedpyright with `reportImplicitOverride = true` enforces the semantic override.
  Ruff `ANN` enforces annotations. No custom Ruff plugin is needed.

### Compilation and generated IDs

- A `Task` never declares its ID.
- `Workflow.add(task, requires=...)` records ordered definitions.
- `Workflow.compile()` is the only authority that assigns stable IDs.
- IDs are deterministic from ordered topology and a normalized task slug, for example
  `001.prepare-source`, `002.build-amd64`, `003.publish-manifest`.
- Duplicate titles are legal because the ordinal disambiguates them.
- A compiled workflow is immutable.
- Journal entries, events, run results, errors, and TUI state all use the compiled
  `task_id`.
- Existing operation-ID lookups in nanoFaaS are removed rather than mapped to a second
  identity.

### Resources without phases

There is one flat `Workflow`; v2 does not introduce `Phase` or `PhasedWorkflow`.

```python
workflow.add(PrepareSource(...))
workflow.add(BuildAmd64(...), requires=(builder_vm,))
workflow.add(PushLocalImage(...), requires=(builder_vm, registry_tunnel))
```

- A resource declares acquire and release tasks.
- The compiler inserts acquire immediately before the first consumer and release
  immediately after the last consumer.
- Inserted acquire/release operations are normal compiled task units and receive
  generated IDs.
- Release operations always run after acquisition, including after a consumer failure,
  and execute in reverse acquisition order.
- Acquisition is non-idempotent by default. A resource must explicitly set
  `acquire_idempotent=True` before a failed or interrupted acquire may be retried.
- Release/finalizer tasks are never journal-reused.
- `keep_infrastructure` may retain explicitly marked infrastructure resources but must
  not retain safety resources such as secret mounts or port forwards.
- Presentation grouping can be added later without changing execution or journal
  semantics, if the TUI demonstrates a real need.

### Journal and resume

```python
workflow.run(
    journal=JournalConfig(path=run_dir / "journal.jsonl"),
    resume=True,
)
```

- Journal is optional.
- `resume=True` without a journal raises `ResumeConfigurationError`.
- The engine creates journal state from the compiled topology. Five compiled tasks
  produce five logical attempt-0 `pending` entries before the first task runs.
- Storage is append-only JSON Lines. A logical task can have multiple physical attempt
  records. Recovery may truncate only a malformed final record without a newline;
  malformed complete or interior records raise `CorruptJournalError`.
- Every record carries a deterministic fingerprint of the workflow ID and ordered
  `(task_id, kind, task class, reusable reuse_key)` definition. A missing or different
  fingerprint raises `WorkflowTopologyMismatchError`; there is no compatibility
  fallback.
- The workflow automatically records `started`, `passed`, `failed`, `skipped`, and
  finalizer outcomes. Tasks and scenarios never call the journal.
- A passed reusable task is skipped only when it has non-empty evidence and every
  entry is verified. Sonata supplies only `file-digest`; all other evidence kinds
  require an injected verifier.
- Passed non-reusable tasks run again.
- `started` without a terminal record is ambiguous:
  - idempotent task: append a new attempt and rerun;
  - non-idempotent task: raise `AmbiguousTaskStateError`.
- Failed or interrupted non-idempotent tasks are never retried automatically.
- Schema v2 stores only generic workflow/task/attempt/evidence data. Release identity,
  semantic versions, registries, and artifact policy do not belong in Sonata.

Example record:

```json
{
  "schema_version": 2,
  "workflow_id": "release",
  "workflow_fingerprint": "sha256:...",
  "run_id": "01J...",
  "task_id": "002.build-amd64",
  "attempt": 1,
  "status": "passed",
  "started_at": "2026-07-24T10:00:00Z",
  "finished_at": "2026-07-24T10:08:00Z",
  "evidence": [
    {"kind": "oci-image", "reference": "registry/image:tag", "digest": "sha256:..."}
  ]
}
```

### Events

- Sonata emits generic workflow/task/resource/journal events.
- Events are produced by the engine around execution; task authors do not need to
  record lifecycle events.
- Sinks are optional and synchronous in v2.
- The nanoFaaS TUI consumes events and owns all Rich/UI behavior.

## Success criteria

- Sonata builds and tests without nanoFaaS on `PYTHONPATH`.
- Static checks reject a task with `run() -> None`.
- Runtime rejects a task that returns `None`.
- One compiled task has one canonical generated ID across result, journal, event, and
  TUI.
- Five compiled tasks create five logical journal entries.
- Verified reusable tasks skip; stale evidence forces execution.
- Empty evidence and unregistered evidence kinds never permit reuse.
- Changed workflow topology or task types cannot reuse an existing journal.
- Ambiguous idempotent tasks rerun; ambiguous non-idempotent tasks fail closed.
- Resource finalizers execute automatically on success and failure.
- Existing nanoFaaS workflows and the new release scenario pass without compatibility
  shims.

---

### Task 1: Freeze the extracted Sonata baseline

**Repository:** `miciav/sonata`

**Files:**
- Verify: `src/sonata_engine/core/*.py`
- Verify: `src/sonata_engine/workflow/*.py`
- Verify: `tests/core/*.py`
- Verify: `tests/workflow/*.py`
- Modify: `README.md`
- Modify: `pyproject.toml`

**Step 1: Install the development environment**

Run: `uv sync --dev`

Expected: the environment resolves with no runtime dependency on nanoFaaS.

**Step 2: Run the copied tests**

Run: `uv run pytest`

Expected: PASS.

**Step 3: Run static checks**

Run: `uv run ruff check . && uv run basedpyright`

Expected: PASS after correcting only extraction/import issues. Do not redesign the API
in this task.

**Step 4: Commit**

```bash
git add README.md pyproject.toml src tests docs
git commit -m "Bootstrap Sonata from nanoFaaS workflow kernel"
```

---

### Task 2: Introduce the mandatory typed task outcome

**Repository:** `miciav/sonata`

**Files:**
- Create: `src/sonata_engine/core/outcome.py`
- Replace: `src/sonata_engine/core/task.py`
- Modify: `src/sonata_engine/core/__init__.py`
- Modify: `src/sonata_engine/__init__.py`
- Replace: `tests/core/test_task.py`
- Create: `tests/typecheck/task_contracts.py`

**Step 1: Write runtime contract tests**

Test that:

```python
class ValidTask(Task[int]):
    title = "Valid"

    def run(self) -> TaskOutcome[int]:
        return TaskOutcome(value=42)
```

is accepted, and the old structural dataclass that does not subclass `Task` is not a
workflow task.

**Step 2: Add a negative static fixture**

Create a task whose override returns `None`. Run:

```bash
uv run basedpyright tests/typecheck/task_contracts.py
```

Expected: FAIL on the invalid override. Convert the negative check into a subprocess
pytest assertion so the normal type-check suite remains green.

**Step 3: Implement the minimal ABC and outcome models**

Implement exactly the contracts in “Task contract”. Export `Evidence`, `TaskOutcome`,
`Task`, and `ReusableTask`.

**Step 4: Verify**

Run: `uv run pytest && uv run ruff check . && uv run basedpyright`

Expected: PASS.

**Step 5: Commit**

```bash
git add src tests
git commit -m "Require typed task outcomes"
```

---

### Task 3: Compile workflows and generate the sole task identity

**Repository:** `miciav/sonata`

**Files:**
- Create: `src/sonata_engine/core/compiled.py`
- Replace: `src/sonata_engine/core/workflow.py`
- Modify: `src/sonata_engine/core/__init__.py`
- Modify: `src/sonata_engine/__init__.py`
- Replace: `tests/core/test_workflow.py`
- Create: `tests/core/test_compiled.py`

**Step 1: Write failing compilation tests**

Cover:

- insertion order is preserved;
- IDs are generated as `{ordinal:03d}.{slug}`;
- duplicate titles receive different ordinal IDs;
- task objects expose no `task_id`;
- compilation is immutable and repeated compilation is deterministic.

**Step 2: Implement the smallest compiler**

Use frozen dataclasses:

```python
@dataclass(frozen=True, slots=True)
class CompiledTask(Generic[T]):
    task_id: str
    task: Task[T]
    required_resources: tuple[Resource, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    workflow_id: str
    tasks: tuple[CompiledTask[object], ...]
```

Keep slug generation local and deterministic. Do not add user-supplied ID overrides.

**Step 3: Verify**

Run: `uv run pytest && uv run ruff check . && uv run basedpyright`

Expected: PASS.

**Step 4: Commit**

```bash
git add src tests
git commit -m "Compile workflows with generated task IDs"
```

---

### Task 4: Move lifecycle events into the engine runner

**Repository:** `miciav/sonata`

**Files:**
- Modify: `src/sonata_engine/workflow/events.py`
- Modify: `src/sonata_engine/workflow/reporting.py`
- Modify: `src/sonata_engine/core/workflow.py`
- Modify: `tests/workflow/test_events.py`
- Modify: `tests/core/test_workflow.py`

**Step 1: Write failing runner tests**

Assert that one task execution emits ordered `task.started` and `task.passed` events
with the compiled ID, while exceptions emit `task.failed`.

**Step 2: Implement automatic emission**

The runner wraps every `task.run()` call. Keep sinks optional. Reject any result that
is not `TaskOutcome` with `InvalidTaskOutcomeError`.

**Step 3: Remove lifecycle reporting from task-facing helpers**

Keep generic log/progress emission only if already used. A task must not be able to
produce a competing lifecycle identity.

**Step 4: Verify and commit**

Run: `uv run pytest && uv run ruff check . && uv run basedpyright`

```bash
git add src tests
git commit -m "Emit task lifecycle from the workflow runner"
```

---

### Task 5: Compile resource acquisition and finalization

**Repository:** `miciav/sonata`

**Files:**
- Replace: `src/sonata_engine/core/resource_task.py`
- Modify: `src/sonata_engine/core/compiled.py`
- Modify: `src/sonata_engine/core/workflow.py`
- Replace: `tests/core/test_resource_cleanup.py`

**Step 1: Write failing topology tests**

For resources used by multiple tasks, assert one acquire before the first consumer
and one release after the last. Cover overlapping resources and stable generated IDs.

**Step 2: Write failing lifecycle tests**

Cover reverse release order, consumer failure, acquire failure, release failure,
infrastructure retention, and mandatory release of safety resources.

**Step 3: Implement resource compilation and finalization**

Represent acquire and release as engine-owned `Task[None]` units. Do not introduce
phases or a second executor.

**Step 4: Verify and commit**

Run: `uv run pytest && uv run ruff check . && uv run basedpyright`

```bash
git add src tests
git commit -m "Compile resource lifecycle into workflows"
```

---

### Task 6: Add optional append-only journal v2

**Repository:** `miciav/sonata`

**Files:**
- Create: `src/sonata_engine/journal.py`
- Create: `src/sonata_engine/errors.py`
- Modify: `src/sonata_engine/core/workflow.py`
- Modify: `src/sonata_engine/__init__.py`
- Create: `tests/test_journal.py`
- Create: `tests/test_resume.py`

**Step 1: Write failing journal topology tests**

Compile a five-task workflow and assert five logical entries. Verify that retries add
physical attempt records without creating a sixth logical task.

**Step 2: Write failing automatic-recording tests**

Assert that tasks never receive a journal object and the runner records started,
passed, failed, skipped, and finalizer outcomes.

**Step 3: Write failing resume matrix tests**

Cover:

| Previous state | Task kind | Expected |
|---|---|---|
| passed + valid evidence | reusable | skip |
| passed + stale evidence | reusable | run |
| passed | ordinary | run |
| started only | idempotent | retry |
| started only | non-idempotent | `AmbiguousTaskStateError` |
| failed/interrupted | non-idempotent | no automatic retry |

Also assert `resume=True` without `JournalConfig` fails and schema v1 is rejected.

**Step 4: Implement JSON Lines storage**

Use the standard library only. Parse all records into task/attempt state before
execution. Initialize the complete topology as `pending`, append and flush each
record, and fsync the pre-execution `started` record.

**Step 5: Implement evidence verification as an injected registry**

Ship only the `file-digest` verifier. `exact-value` and domain-specific OCI or release
verification require explicitly injected downstream verifiers.

**Step 6: Verify and commit**

Run: `uv run pytest && uv run ruff check . && uv run basedpyright`

```bash
git add src tests
git commit -m "Add automatic journal v2 and resume"
```

---

### Task 7: Enforce Sonata independence

**Repository:** `miciav/sonata`

**Files:**
- Create: `tests/test_package_boundaries.py`
- Modify: `pyproject.toml`
- Create: `.github/workflows/ci.yml`

**Step 1: Write a source-boundary test**

Walk `src/sonata_engine/**/*.py` with `ast` and fail imports whose root is in:

```python
FORBIDDEN = {
    "controlplane_tool",
    "nanofaas",
    "workflow_tasks",
    "azure_vm_sdk",
    "multipass_sdk",
    "proxmox_sdk",
}
```

Also assert no runtime dependency appears in `project.dependencies`.

**Step 2: Add minimal CI**

Run on Python 3.12:

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run basedpyright
```

**Step 3: Verify and commit**

Run the same four commands locally, then:

```bash
git add pyproject.toml .github tests
git commit -m "Enforce Sonata package boundaries"
```

---

### Task 8: Make the nanoFaaS task library depend on Sonata

**Repository:** `miciav/nanofaas`

**Files:**
- Modify: root `pyproject.toml`
- Modify: `tools/workflow-tasks/pyproject.toml`
- Modify: task modules under `tools/workflow-tasks/src/workflow_tasks/`
- Modify: matching tests under `tools/workflow-tasks/tests/`

**Step 1: Add Sonata as a pinned workspace/source dependency**

During coordinated development use a local uv source. Before merge, pin an immutable
Sonata tag or commit.

**Step 2: Migrate task classes**

Every task subclasses `Task[T]` or `ReusableTask` and returns `TaskOutcome[T]`.
Existing non-reusable tasks may preserve their runtime values through
`TaskOutcome.value`.

**Step 3: Delete task-declared IDs**

Remove `task_id`, operation ID adapters, and string-based result lookup. Pass required
runtime values explicitly through composition-owned references.

**Step 4: Add static architecture tests**

Assert:

- all concrete task classes subclass Sonata `Task`;
- no concrete `run()` returns `Any` or `None`;
- reusable tasks have no mutable `.result` field;
- `workflow_tasks` does not import `controlplane_tool`.

**Step 5: Verify and commit**

Run:

```bash
uv run --project tools/workflow-tasks pytest
uv run --project tools/workflow-tasks ruff check .
uv run --project tools/workflow-tasks basedpyright
uv run --project tools/controlplane pytest
```

Commit only after `gitnexus_detect_changes(scope="all")` confirms the expected blast
radius.

---

### Task 9: Migrate existing nanoFaaS workflows with no compatibility layer

**Repository:** `miciav/nanofaas`

**Files:**
- Modify: workflow builders under `tools/workflow-tasks/src/workflow_tasks/workflows/`
- Modify: `tools/controlplane/src/controlplane_tool/cli/provisioning.py`
- Modify: workflow/TUI adapters under `tools/controlplane/src/controlplane_tool/`
- Modify: matching tests

**Step 1: Characterize current workflows**

Before editing, use GitNexus impact analysis on `Workflow`, `Task`, and each builder.
Record the current task order, cleanup behavior, returned values, and event IDs.

**Step 2: Rewrite builders against Sonata**

Use flat `Workflow.add(...)` calls and explicit resource requirements. Delete
operation-ID lookups instead of translating them.

**Step 3: Adapt the generic TUI consumer**

Consume Sonata events keyed only by compiled `task_id`. Keep release knowledge out of
the renderer.

**Step 4: Verify**

Run both full Python suites and the specific CLI smoke tests for every migrated
workflow.

**Step 5: Commit**

Run `gitnexus_detect_changes(scope="all")`, stage only migration files, and commit:

```bash
git commit -m "Migrate nanoFaaS workflows to Sonata"
```

---

### Task 10: Implement nanoFaaS release tasks

**Repository:** `miciav/nanofaas`

**Files:**
- Create: `tools/workflow-tasks/src/workflow_tasks/release/__init__.py`
- Create: `tools/workflow-tasks/src/workflow_tasks/release/tasks.py`
- Create: `tools/workflow-tasks/src/workflow_tasks/release/evidence.py`
- Create: `tools/workflow-tasks/tests/release/test_tasks.py`
- Modify: existing release helpers only where behavior is promoted into a task

**Step 1: Inventory the current release runner**

Map each existing release action to one task. Preserve order, gates, publication
semantics, and cleanup behavior. Do not move CLI parsing or TUI code into the task
library.

**Step 2: Write task characterization tests**

Use fakes at remote-exec, registry, filesystem, and clock boundaries. Each task test
asserts its `TaskOutcome`, evidence, idempotency, and reusability classification.

**Step 3: Implement the minimal task set**

Create release-specific tasks for source verification, builds, registry pushes,
benchmarks, aggregation, regression gate, smoke checks, manifest/alias publication,
attestation, and finalization.

**Step 4: Classify reuse conservatively**

Only tasks with independently verifiable evidence subclass `ReusableTask`.
Publication and reconciliation tasks should generally be idempotent, non-reusable.

**Step 5: Verify and commit**

Run the task-library suite, Ruff, Basedpyright, and GitNexus change detection before:

```bash
git commit -m "Add nanoFaaS release workflow tasks"
```

---

### Task 11: Assemble release as a controlplane workflow

**Repository:** `miciav/nanofaas`

**Files:**
- Create: `tools/controlplane/src/controlplane_tool/release/scenario.py`
- Modify: `tools/controlplane/src/controlplane_tool/cli/release.py`
- Delete: bespoke orchestration from `tools/controlplane/src/controlplane_tool/release/run.py`
- Modify: release and CLI tests

**Step 1: Write a topology test**

Assert the compiled workflow contains the existing release operations in the existing
order, with resource acquire/release units at the correct boundaries and canonical
generated IDs.

**Step 2: Write CLI behavior tests**

Cover default run, optional journal, resume, invalid resume configuration,
`keep_infrastructure`, event sink wiring, and propagated task failure.

**Step 3: Build the scenario**

`controlplane_tool` constructs nanoFaaS release tasks, declares their resource needs,
compiles the Sonata workflow, and calls `run(...)`. It does not record journal entries.

**Step 4: Remove the bespoke runner**

Delete only orchestration made redundant by Sonata. Retain product policy and helper
functions in nanoFaaS-owned modules.

**Step 5: Verify and commit**

Run:

```bash
uv run --project tools/workflow-tasks pytest
uv run --project tools/controlplane pytest
uv run --project tools/controlplane ruff check .
uv run --project tools/controlplane basedpyright
```

Then run GitNexus change detection and commit:

```bash
git commit -m "Run releases as Sonata workflows"
```

---

### Task 12: End-to-end resume and failure validation

**Repositories:** `miciav/sonata`, `miciav/nanofaas`

**Files:**
- Create or modify: Sonata resume integration tests
- Create or modify: nanoFaaS release integration tests
- Modify: user documentation for release and journal v2

**Step 1: Validate the five-task journal invariant**

Run a five-task fixture and inspect the journal: five logical entries, canonical IDs,
and automatic lifecycle records.

**Step 2: Validate interruption behavior**

Inject interruption:

- before an idempotent task finishes, then resume and observe a new attempt;
- before a non-idempotent task finishes, then resume and observe
  `AmbiguousTaskStateError`;
- after a reusable task passes, then resume with valid and stale evidence.

**Step 3: Validate release behavior**

Run the release integration suite with fake external boundaries. Assert the same gates,
publication order, artifact references, and finalizers as the characterized runner.

**Step 4: Validate dependency direction**

Install and test Sonata alone. Then install nanoFaaS task library with Sonata, and
finally controlplane-tool with both. No reverse import may be required.

**Step 5: Final checks**

Run all repository checks, `git diff --check`, and GitNexus change detection in
nanoFaaS. Rebuild the GitNexus index after the final nanoFaaS commit.
