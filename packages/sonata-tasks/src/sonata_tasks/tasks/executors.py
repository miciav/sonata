"""Compatibility import for injected-runner adapters."""

from sonata_tasks.execution.adapters import (
    HostCommandTaskExecutor,
    VmCommandTaskExecutor,
)
from sonata_tasks.execution.ports import (
    CommandRunResult,
    HostCommandRunner,
    VmCommandRunner,
)

VmCommandResult = CommandRunResult

__all__ = [
    "CommandRunResult",
    "HostCommandRunner",
    "HostCommandTaskExecutor",
    "VmCommandResult",
    "VmCommandRunner",
    "VmCommandTaskExecutor",
]
