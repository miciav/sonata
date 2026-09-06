from __future__ import annotations

from shellcraft.backend import ShellExecutionResult


def successful_result(command: list[str], *, stdout: str = "") -> ShellExecutionResult:
    return ShellExecutionResult(command=command, return_code=0, stdout=stdout)
