from __future__ import annotations


class InvalidTaskOutcomeError(Exception):
    """Raised when a compiled task's `run()` returns something other than `TaskOutcome`."""


class ResumeConfigurationError(Exception):
    """Raised when `resume=True` is requested without a `JournalConfig`."""


class AmbiguousTaskStateError(Exception):
    """Raised when a journal's recorded state cannot be resumed automatically and safely.

    Covers both a non-idempotent task interrupted after `started` with no terminal
    record, and a non-idempotent task with a genuinely recorded `failed`: resuming
    past either without human intervention is exactly the unsafe automatic action
    this error exists to prevent.
    """


class UnsupportedJournalSchemaError(Exception):
    """Raised when a journal record declares a `schema_version` this engine cannot read."""
