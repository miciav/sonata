from __future__ import annotations

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
