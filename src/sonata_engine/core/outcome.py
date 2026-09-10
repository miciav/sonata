"""What a task reports when it finishes.

A `TaskOutcome` pairs the value a task produced with the `Evidence` that
justifies reusing it on a later run. The value never leaves the process; the
evidence is what the journal stores and what resume re-verifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Evidence:
    """A checkable claim that a task's output still exists and is unchanged.

    `kind` names the verifier that knows how to check it (for example
    `file-digest`), `reference` is what that verifier looks at, and `digest`
    is the optional expected fingerprint. On resume, every entry of a reusable
    task must verify before its evidence is trusted.
    """

    kind: str
    reference: str
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class TaskOutcome[T]:
    """The value a task returned, plus the evidence a later resume can check.

    `value` is an in-process data channel and is never journalled, so a skipped
    task is reconstructed with `None` rather than its original value.
    """

    value: T | None = None
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
