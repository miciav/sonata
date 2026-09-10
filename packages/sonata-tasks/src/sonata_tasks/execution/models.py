"""Command options, task specifications, and their results."""

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
    """Options controlling how one command task is executed.

    ``cwd``, ``env``, and ``timeout_seconds`` shape a local run, while
    ``remote_dir`` names a directory inside a remote target. A run passes when
    its return code appears in ``expected_exit_codes``.
    """

    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    remote_dir: str | None = None
    expected_exit_codes: frozenset[int] = frozenset({0})
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        """Validate the fields and store ``env`` and exit codes immutably.

        Raises:
            ValueError: If ``timeout_seconds`` is not finite and positive, or
                ``expected_exit_codes`` is empty.
            TypeError: If ``expected_exit_codes`` holds non-integers or ``env``
                contains non-string keys or values.

        """
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and greater than zero")
        codes = frozenset(self.expected_exit_codes)
        if not codes:
            raise ValueError("expected_exit_codes must not be empty")
        if not all(
            isinstance(code, int) and not isinstance(code, bool) for code in codes
        ):
            raise TypeError("expected_exit_codes must contain integers")
        env = dict(self.env)
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in env.items()
        ):
            raise TypeError("env must map strings to strings")
        object.__setattr__(
            self, "cwd", Path(self.cwd) if self.cwd is not None else None
        )
        object.__setattr__(self, "env", MappingProxyType(env))
        object.__setattr__(self, "expected_exit_codes", codes)


@dataclass(frozen=True, slots=True)
class CommandTaskSpec:
    """A resolved command task handed to an executor.

    ``task_id`` and ``summary`` label the task for reporting, ``argv`` is the
    program-and-arguments tuple to execute, and ``role`` names the executor
    binding that should run it.
    """

    task_id: str
    summary: str
    argv: tuple[str, ...]
    role: str = "host"
    options: CommandOptions = field(default_factory=CommandOptions)

    def __post_init__(self) -> None:
        """Normalize ``argv`` to a tuple and check the program name and role.

        Raises:
            ValueError: If ``argv`` is empty, holds a non-string part, or
                starts with an empty program name, or if ``role`` is empty.

        """
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
        """Return the role naming the executor that should run this task."""
        return self.role


@dataclass(frozen=True, slots=True)
class TaskResult:
    """The outcome of running one command task.

    ``status`` is ``"passed"`` when ``return_code`` was expected and
    ``"failed"`` otherwise, alongside the captured ``stdout`` and ``stderr``.
    """

    task_id: str
    status: TaskStatus
    return_code: int | None = None
    expected_exit_codes: frozenset[int] = frozenset({0})
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """Return whether the task passed with a genuine expected exit code.

        A boolean return code does not count, even though ``bool`` is an
        ``int`` subclass.
        """
        return (
            self.status == "passed"
            and isinstance(self.return_code, int)
            and not isinstance(self.return_code, bool)
            and self.return_code in self.expected_exit_codes
        )
