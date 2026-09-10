"""Adapters that present a VM orchestrator as a VM lifecycle."""

from __future__ import annotations

from sonata_tasks.vm.models import (
    VmConfig,
    VmInfo,
    VmLifecycle,
    VmRequest,
    vm_remote_home,
)
from sonata_tasks.vm.ports import VmOrchestratorProtocol


class VmLifecycleAdapter:
    """Drive a VmOrchestratorProtocol through the VmLifecycleProtocol tasks use."""

    def __init__(
        self,
        orchestrator: VmOrchestratorProtocol,
        *,
        lifecycle: VmLifecycle,
        credentials: VmRequest | None = None,
    ) -> None:
        """Store the orchestrator and the request template reused for every call.

        ``credentials`` carries provider settings such as the Azure resource
        group or the Proxmox host; when omitted it is rebuilt as a bare
        ``VmRequest`` for ``lifecycle``, which is all Multipass needs.
        """
        self._vm = orchestrator
        self._lifecycle = lifecycle
        self._credentials = credentials or VmRequest(lifecycle=lifecycle)

    def ensure_running(self, config: VmConfig) -> VmInfo:
        """Start or adopt the VM and return the details needed to reach it.

        The stored credentials are copied and overridden with the name and
        sizing from ``config``. Raises RuntimeError carrying the provider's
        stderr (falling back to stdout) when the lifecycle command fails.
        """
        request = self._credentials.model_copy(
            update={
                "name": config.name,
                "cpus": config.cpus,
                "memory": config.memory,
                "disk": config.disk,
            }
        )
        result = self._vm.ensure_running(request)
        if not isinstance(result.return_code, int) or result.return_code != 0:
            detail = result.stderr or result.stdout or "VM lifecycle preflight failed"
            raise RuntimeError(detail.strip())
        return VmInfo(
            config.name,
            self._vm.connection_host(request),
            request.user,
            vm_remote_home(request),
        )

    def destroy(self, info: VmInfo) -> None:
        """Tear down the VM named by ``info``.

        Raises RuntimeError carrying the provider's stderr (falling back to
        stdout) when teardown fails.
        """
        result = self._vm.teardown(
            self._credentials.model_copy(update={"name": info.name})
        )
        if not isinstance(result.return_code, int) or result.return_code != 0:
            detail = result.stderr or result.stdout or "VM teardown failed"
            raise RuntimeError(detail.strip())


def multipass_vm_adapter(orchestrator: VmOrchestratorProtocol) -> VmLifecycleAdapter:
    """Return a lifecycle adapter that drives Multipass VMs."""
    return VmLifecycleAdapter(orchestrator, lifecycle="multipass")


def azure_vm_adapter(
    orchestrator: VmOrchestratorProtocol, *, credentials: VmRequest | None = None
) -> VmLifecycleAdapter:
    """Return a lifecycle adapter that drives Azure VMs.

    ``credentials`` supplies the resource group, location and SSH key. Without
    them the orchestrator is called with a request built from defaults alone,
    which then also fixes those settings for teardown.
    """
    return VmLifecycleAdapter(orchestrator, lifecycle="azure", credentials=credentials)


def proxmox_vm_adapter(
    orchestrator: VmOrchestratorProtocol, *, credentials: VmRequest | None = None
) -> VmLifecycleAdapter:
    """Return a lifecycle adapter that drives Proxmox VMs.

    ``credentials`` supplies the Proxmox host, node and authentication. Without
    them the orchestrator is called with a request built from defaults alone,
    which then also fixes those settings for teardown.
    """
    return VmLifecycleAdapter(
        orchestrator, lifecycle="proxmox", credentials=credentials
    )
