# Sonata Review Findings Remediation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Sonata's v2 API single-source, preserve task outcomes, and make resource cleanup and journal resume fail-safe.

**Architecture:** `Workflow` has one builder/compiler/runner API and returns immutable execution results keyed by compiler-owned task IDs. `Journal` is initialized from the complete compiled topology, validates a deterministic topology fingerprint, and records every compiled unit before execution. Resume and resource lifecycle decisions remain engine-owned and fail closed.

**Tech Stack:** Python 3.12+, standard library, pytest, Ruff, Basedpyright.

---

### Task 1: Replace the legacy executor with the sole v2 API

**Files:**
- Modify: `src/sonata_engine/core/compiled.py`
- Modify: `src/sonata_engine/core/workflow.py`
- Modify: `src/sonata_engine/core/__init__.py`
- Modify: `src/sonata_engine/__init__.py`
- Modify: `tests/core/test_compiled.py`
- Replace: `tests/core/test_workflow.py`
- Modify: `tests/core/test_resource_cleanup.py`
- Modify: `tests/test_journal.py`
- Modify: `tests/test_resume.py`

**Step 1: Write failing API and result tests**

Test that `Workflow(workflow_id="wf")` is the only constructor shape, `run()` compiles and executes automatically, and it returns an immutable `WorkflowResult` containing one `TaskExecution` per compiled unit. An executed consumer exposes its `TaskOutcome.value`; a skipped task has `outcome=None`.

**Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest tests/core/test_workflow.py tests/core/test_compiled.py -q
```

Expected: failures because the legacy `tasks` argument is required, `run()` is legacy, and result types do not exist.

**Step 3: Implement the minimal v2 API**

- Remove `tasks`, `cleanup_tasks`, `task_ids`, `phase_titles`, and the legacy execution loop.
- Make `workflow_id` required.
- Make `run(journal=None, resume=False, verifiers=None) -> WorkflowResult` compile and execute `_definitions`.
- Keep compiled execution private.
- Add frozen `TaskExecution(task_id, status, outcome)` and `WorkflowResult(workflow_id, tasks)`.
- Reject non-`None` `TaskOutcome.value` from `ReusableTask`.

**Step 4: Run focused and full tests**

Run:

```bash
uv run pytest tests/core -q
uv run pytest -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src tests
git commit -m "Replace legacy execution with the Sonata v2 API"
```

---

### Task 2: Initialize and validate journal topology

**Files:**
- Modify: `src/sonata_engine/core/compiled.py`
- Modify: `src/sonata_engine/core/workflow.py`
- Modify: `src/sonata_engine/journal.py`
- Modify: `src/sonata_engine/errors.py`
- Modify: `src/sonata_engine/__init__.py`
- Modify: `tests/test_journal.py`
- Modify: `tests/test_resume.py`

**Step 1: Write failing topology tests**

Test that:

- three compiled tasks create three logical `pending` entries before the first task runs;
- an unreachable third task remains represented after task two fails;
- a retained infrastructure finalizer is recorded as `skipped`;
- changing task type or ordered topology raises `WorkflowTopologyMismatchError`.

**Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_journal.py -q
```

Expected: failures because `Journal` currently learns tasks only from lifecycle writes and has no topology fingerprint.

**Step 3: Implement topology initialization**

- Compute `CompiledWorkflow.fingerprint` from workflow ID and the ordered tuple of
  compiled task ID, kind, task class fully-qualified name, and the mandatory
  `reuse_key` of each reusable task.
- Construct `Journal` with the entire `CompiledWorkflow`.
- Include `workflow_fingerprint` in every record.
- On a new workflow journal, append attempt `0`, status `pending`, for every compiled task.
- Treat `pending` as a fresh runnable state.
- Reject an existing record whose fingerprint differs.

**Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_journal.py tests/test_resume.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src tests
git commit -m "Create journals from compiled workflow topology"
```

---

### Task 3: Make journal recovery and reuse fail closed

**Files:**
- Modify: `src/sonata_engine/journal.py`
- Modify: `src/sonata_engine/errors.py`
- Modify: `src/sonata_engine/__init__.py`
- Modify: `tests/test_journal.py`
- Modify: `tests/test_resume.py`

**Step 1: Write failing recovery and evidence tests**

Test that:

- an incomplete final JSON line is ignored and the durable prior state is used;
- malformed interior or newline-terminated records raise `CorruptJournalError`;
- empty evidence never permits reuse;
- `exact-value` without an injected verifier never permits reuse;
- an injected verifier can permit reuse;
- a failed or interrupted acquire retries only when `acquire_idempotent=True`.

**Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_journal.py tests/test_resume.py -q
```

Expected: failures for torn-tail parsing, vacuous evidence verification, unconditional exact-value verification, and failed acquire retry.

**Step 3: Implement the minimal safety rules**

- Ignore only a malformed, non-newline-terminated final record.
- Wrap other parse/shape errors in `CorruptJournalError`.
- Require at least one evidence item and a registered verifier for every item.
- Keep only `file-digest` as a built-in verifier.
- Add `Resource.acquire_idempotent: bool = False` and propagate it to `ResourceOp`.
- Use the normal journal decision matrix for acquire units.

**Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_journal.py tests/test_resume.py tests/core/test_resource_cleanup.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src tests
git commit -m "Make journal resume fail closed"
```

---

### Task 4: Guarantee cleanup and observable skips

**Files:**
- Modify: `src/sonata_engine/core/workflow.py`
- Modify: `src/sonata_engine/workflow/reporting.py`
- Modify: `tests/core/test_resource_cleanup.py`
- Modify: `tests/core/test_workflow.py`
- Modify: `tests/test_resume.py`
- Modify: `tests/workflow/test_reporting.py`

**Step 1: Write failing lifecycle tests**

Test that:

- an acquired resource is released if terminal event emission fails;
- an acquired resource is released if `Journal.record_passed()` fails;
- resume emits `task.skipped` with the canonical compiled ID;
- retained infrastructure emits and journals `task.skipped`;
- remaining finalizers run even when a prior finalizer or its journal write fails.

**Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/core/test_resource_cleanup.py tests/test_resume.py tests/workflow/test_reporting.py -q
```

Expected: failures reproducing the resource leak and missing skip events.

**Step 3: Implement cleanup registration and skip emission**

- Register a resource as acquired immediately after its acquire callable returns, before outcome validation, terminal event emission, or terminal journal writes.
- Add runner-owned `task.skipped` emission.
- Record retained finalizers as skipped.
- Preserve the primary exception while collecting all cleanup/reporting failures.

**Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/core/test_resource_cleanup.py tests/test_resume.py tests/workflow/test_reporting.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src tests
git commit -m "Guarantee resource cleanup and observable skips"
```

---

### Task 5: Remove stale compatibility surfaces and verify

**Files:**
- Modify: `src/sonata_engine/workflow/reporting.py`
- Modify: `src/sonata_engine/workflow/event_builders.py`
- Modify: `tests/workflow/test_reporting.py`
- Modify: `tests/workflow/test_event_builders.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/plans/2026-07-24-release-on-workflow-engine.md`

**Step 1: Write or update public-surface tests**

Assert that lifecycle is engine-owned, no phase API or user-supplied task lifecycle helper remains public, and the root package exports the final v2 types and errors.

**Step 2: Remove stale surfaces**

- Remove `phase`, `workflow_step`, and `build_phase_event`.
- Remove the obsolete Ruff `ANN401` ignore.
- Document the single `Workflow.run()` API, outcomes, topology fingerprint, journal behavior, and explicit resource idempotency.

**Step 3: Run complete verification**

Run:

```bash
uv run pytest
uv run ruff check --no-cache .
uv run basedpyright
git diff --check
```

Expected: all checks pass with no warnings.

**Step 4: Commit**

```bash
git add README.md docs pyproject.toml src tests
git commit -m "Document the hardened Sonata v2 contract"
```

**Step 5: Request independent code review**

Review the complete branch diff against this plan and resolve every P1/P2 finding before integration.
