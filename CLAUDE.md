# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --dev                                # install (uses uv, Python >= 3.12)
uv run pytest                                # all tests (coverage on by default)
uv run pytest tests/core/test_workflow.py    # one file
uv run pytest tests/core/test_workflow.py::test_name   # one test
uv run ruff check .                          # lint (ANN, E, F, I; line length 100)
uv run basedpyright                          # type check (standard mode, reportImplicitOverride)
```

## What this is

Sonata (`sonata-engine` distribution, `sonata_engine` import) is a product-independent Python workflow engine extracted from `miciav/nanofaas`. It has **zero runtime dependencies** — keep it that way.

**Hard boundary:** Sonata must never import anything from `nanofaas`, `controlplane_tool`, VM providers, Ansible, load-test, release, or remote-execution code. Product-specific tasks stay in nanoFaaS; workflow assembly stays in `controlplane-tool`. Both depend on Sonata, never the reverse.

## Architecture

Two layers under `src/sonata_engine/`:

- **`core/`** — execution. `Task[T]` is a generic ABC whose `run()` returns
  `TaskOutcome[T]`. `Workflow.add()` builds a sequential workflow;
  `Workflow.compile()` assigns canonical `NNN.slug` IDs and inserts acquire and
  release units for declared `Resource`s; `Workflow.run()` executes the compiled
  graph, releases resources on success or failure, and optionally journals for
  resume. `Steps` assembles ordinary tasks into one compiled unit with
  individually journalled child steps and in-process upstream values.

- **`workflow/`** — observability. Frozen-dataclass `WorkflowEvent`s are emitted
  to a `WorkflowSink` Protocol (`emit`/`status`). The sink and current
  `WorkflowContext` live in ContextVars with a shared fallback for worker-thread
  visibility. The runner owns task lifecycle events; hand-written tasks may use
  `workflow_log`, `status`, and the reporting-only `subtask` context manager.
  Emission is a no-op when no sink is bound.

The core runner calls the private lifecycle helpers in `workflow/reporting.py`;
tasks never emit their own top-level lifecycle events.

Tests mirror the source layout (`tests/core/`, `tests/workflow/`).

## Design documents

The v2 execution design is recorded in
`docs/plans/2026-07-24-release-on-workflow-engine.md`. Composite-task behavior
and implementation are documented in
`docs/specs/2026-07-28-composite-task-design.md` and
`docs/plans/2026-07-28-composite-task.md`. Read the relevant document before
changing public APIs or journal semantics.
