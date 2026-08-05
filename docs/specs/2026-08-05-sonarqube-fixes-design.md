# SonarQube Findings Fixes — Design

Date: 2026-08-05. Fixes the 15 open issues reported by the first
`scripts/sonar.sh` run (project `sonata-python`).

## Context

The first SonarQube analysis of the repository reported 15 open issues:
5× S3776 (cognitive complexity > 15), 6× S5754 (broad `except` without
re-raise), 2× S5886 (type-hint mismatch), 1× S1066 (mergeable `if`s),
1× S3358 (nested ternary). The goal is 0 open issues with **no behavioral
change**: no public API changes, no error-handling semantics changes.

## Fixes

### 1. Trivial, behavior-identical

| Issue | Site | Fix |
|---|---|---|
| S1066 | `core/workflow.py:86` (`_execute_recorded`) | Merge `if jrnl is not None and resume:` + `if jrnl.decide_task(...) == "skip":` into one condition |
| S3358 | `workflow/event_builders.py:18` (`_resolve_context_fields`) | Nested ternary → `if/elif/else` |
| S5886 | `core/workflow.py:135, 338` (`make_inputs` closures) | `cast(TaskInputs, replace(...))` (import `cast`); `dataclasses.replace` is typed as returning `DataclassInstance` |

### 2. S5754 — broad catches

- `core/workflow.py:112` (`_execute_recorded`): narrow the inner
  `except BaseException` around `jrnl.record_failed` to `OSError` — journal
  writes are file I/O; this matches the existing `_record_release_outcome`
  pattern (`core/workflow.py:363-374`), which already catches only `OSError`.
- NOSONAR with a justification comment (catch-all is the documented design —
  "release errors never abort remaining releases") on:
  - `core/workflow.py:252` — main-walk catch; stops the walk so pending
    releases run, then re-raises after cleanup (invisible to the analyzer
    across the loop).
  - `core/workflow.py:416` — `_task_skipped` emit in the retention-skip path;
    a reporting failure must not abort the skip bookkeeping.
  - `core/workflow.py:435` — release-body failure; collect and continue so
    every pending release still runs.
  - `retention.py:100` — same collector in `release_retained`.
  - `workflow/reporting.py:91` — sink failure is noted on the original task
    exception, which is re-raised; narrowing would let a broken sink's
    exception mask the task failure.

### 3. S3776 — cognitive complexity, behavior-preserving extraction

If a helper still exceeds 15 after extraction, NOSONAR with justification.

- `_execute_recorded` (17 → ~9): resume-skip block →
  module-level `_maybe_resume_skip(task_id, task, jrnl) -> TaskExecution | None`.
- `_run_compiled` (35 → ~13): pending-release loop on failure →
  `_release_pending(...)`; final error combination/raise → `_raise_final(...)`.
- `_release` (21 → ~12): retention-skip branch →
  `_retention_skip(...) -> TaskExecution | None`.
- `_merge_resources` (22 → ~14): the `register` DFS closure → helper.
- `journal._load` (38 → ~12 + ~8 + ~8): `_parse_record` (decode/json/version/
  fingerprint checks) + `_fold_record` (task-field parsing, status check,
  state merge).

## Verification

- `uv run ruff check .`, `uv run basedpyright`, `uv run pytest` — before and
  after, no new failures.
- `scripts/sonar.sh` — expect 0 open issues (NOSONAR-suppressed lines do not
  count). If a suppression is mis-placed and a rule still fires, fix the
  placement and re-run.
- No new tests: the existing suite already covers every touched path (torn
  tail, corrupt journal, resume mismatch, retention, release error
  collection). The refactors keep each branch identical.

## Out of scope

- No quality-gate setup (see `docs/sonarqube.md` — ephemeral server).
- No coverage import for SonarQube.
- No changes to `tests/` analysis (Sonar analyses `src/` only).
