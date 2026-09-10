from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

TaskStatus = Literal["pending", "running", "passed", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class CommandOptions:
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    remote_dir: str | None = None
    expected_exit_codes: frozenset[int] = frozenset({0})
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and greater than zero")
        codes = frozenset(self.expected_exit_codes)
        if not codes:
            raise ValueError("expected_exit_codes must not be empty")
        if not all(isinstance(code, int) and not isinstance(code, bool) for code in codes):
            raise TypeError("expected_exit_codes must contain integers")
        env = dict(self.env)
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
            raise TypeError("env must map strings to strings")
        object.__setattr__(self, "cwd", Path(self.cwd) if self.cwd is not None else None)
        object.__setattr__(self, "env", MappingProxyType(env))
        object.__setattr__(self, "expected_exit_codes", codes)


@dataclass(frozen=True, slots=True)
class CommandTaskSpec:
    task_id: str
    summary: str
    argv: tuple[str, ...]
    role: str = "host"
    options: CommandOptions = field(default_factory=CommandOptions)

    def __post_init__(self) -> None:
        argv = tuple(self.argv)
        if (
            not argv
            or not isinstance(argv[0], str)
            or not argv[0]
            or any(not isinstance(part, str) for part in argv)
        ):
            raise ValueError("argv must start with a non-empty program name")
        if not self.role:
            raise ValueError("role must not be empty")
        object.__setattr__(self, "argv", argv)

    @property
    def execution_role(self) -> str:
        return self.role


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    status: TaskStatus
    return_code: int | None = None
    expected_exit_codes: frozenset[int] = frozenset({0})
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.status == "passed"
            and isinstance(self.return_code, int)
            and not isinstance(self.return_code, bool)
            and self.return_code in self.expected_exit_codes
        )
