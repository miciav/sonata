from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class K6Stage:
    duration: str
    target: int


@dataclass(frozen=True, slots=True)
class K6Config:
    script_path: Path
    summary_output_path: Path
    stages: tuple[K6Stage, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    vus: int | None = None
    duration: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "script_path", Path(self.script_path))
        object.__setattr__(self, "summary_output_path", Path(self.summary_output_path))
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True, slots=True)
class K6RunResult:
    summary_path: Path
    started_at: datetime
    ended_at: datetime
    passed: bool
