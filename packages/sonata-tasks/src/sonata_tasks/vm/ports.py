"""Protocols the VM layer depends on, kept free of any concrete provider."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sonata_tasks.transfer import RemoteOperationResult
from sonata_tasks.vm.models import VmConfig, VmInfo, VmRequest


class VmLifecycleProtocol(Protocol):
    """Lifecycle operations a VM resource performs on a single VM."""

    def ensure_running(self, config: VmConfig) -> VmInfo:
        """Ensure the VM described by ``config`` is running and return its details."""
        ...

    def destroy(self, info: VmInfo) -> None:
        """Tear down the VM described by ``info``."""
        ...


class VmOrchestratorProtocol(Protocol):
    """Provider operations expressed as a single ``VmRequest``."""

    def ensure_running(self, request: VmRequest) -> RemoteOperationResult:
        """Ensure the VM named by ``request`` is running."""
        ...

    def connection_host(self, request: VmRequest) -> str:
        """Return the host at which the VM's SSH service can be reached."""
        ...

    def teardown(self, request: VmRequest) -> RemoteOperationResult:
        """Tear down the VM named by ``request``."""
        ...


class VmCommandProvider(Protocol):
    """Command execution and file transfer against a VM."""

    def exec_argv(
        self,
        request: VmRequest,
        argv: tuple[str, ...] | list[str],
        *,
        env: dict[str, str] | None = None,
        remote_dir: str | None = None,
        dry_run: bool = False,
    ) -> RemoteOperationResult:
        """Run ``argv`` on the VM and return the command's captured output."""
        ...

    def transfer_to(
        self, request: VmRequest, *, source: Path, destination: str
    ) -> RemoteOperationResult:
        """Copy the local file ``source`` to ``destination`` on the VM."""
        ...

    def transfer_from(
        self, request: VmRequest, *, source: str, destination: Path
    ) -> RemoteOperationResult:
        """Copy ``source`` on the VM to the local path ``destination``."""
        ...
