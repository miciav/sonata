"""Errors raised by reusable task implementations."""

from __future__ import annotations


class UnsupportedCommandOptionError(ValueError):
    """An executor cannot honour one of the requested command options."""


class CommandTimeoutError(TimeoutError):
    """A command exceeded its deadline and its process group was stopped."""

    def __init__(
        self,
        argv: tuple[str, ...],
        timeout_seconds: float,
        *,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Record the timed-out command and whatever output it produced.

        The message names ``argv`` and ``timeout_seconds``; the arguments and
        the captured ``stdout`` and ``stderr`` are also kept as attributes for
        callers that want to inspect them.
        """
        super().__init__(f"command {argv!r} exceeded its {timeout_seconds:g}s timeout")
        self.argv = argv
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout
        self.stderr = stderr
