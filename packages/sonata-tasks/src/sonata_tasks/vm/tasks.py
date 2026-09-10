"""Task wrappers that create and destroy the VM a run executes on."""

from __future__ import annotations

from dataclasses import dataclass, field

from sonata_tasks.vm.models import VmConfig, VmInfo
from sonata_tasks.vm.ports import VmLifecycleProtocol


@dataclass
class EnsureVmRunning:
    """Bring up the configured VM and expose the resulting connection info."""

    task_id: str
    title: str
    lifecycle: VmLifecycleProtocol
    config: VmConfig
    _result: VmInfo | None = field(default=None, init=False, repr=False, compare=False)

    def run(self) -> VmInfo:
        """Create or reuse the VM and return its connection info."""
        self._result = self.lifecycle.ensure_running(self.config)
        return self._result

    @property
    def result(self) -> VmInfo:
        """Return the connection info captured by the last :meth:`run` call.

        Raises:
            RuntimeError: If the task has not been run yet.

        """
        if self._result is None:
            raise RuntimeError(f"task {self.task_id!r} has not run yet")
        return self._result


@dataclass
class DestroyVm:
    """Tear down a VM previously described by a :class:`VmInfo`."""

    task_id: str
    title: str
    lifecycle: VmLifecycleProtocol
    info: VmInfo

    def run(self) -> None:
        """Destroy the VM described by ``info``."""
        self.lifecycle.destroy(self.info)
