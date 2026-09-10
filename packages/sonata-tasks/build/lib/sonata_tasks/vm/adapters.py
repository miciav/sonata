from __future__ import annotations

from sonata_tasks.vm.models import VmConfig, VmInfo, VmLifecycle, VmRequest, vm_remote_home
from sonata_tasks.vm.ports import VmOrchestratorProtocol


class VmLifecycleAdapter:
    def __init__(
        self,
        orchestrator: VmOrchestratorProtocol,
        *,
        lifecycle: VmLifecycle,
        credentials: VmRequest | None = None,
    ) -> None:
        self._vm = orchestrator
        self._lifecycle = lifecycle
        self._credentials = credentials or VmRequest(lifecycle=lifecycle)

    def ensure_running(self, config: VmConfig) -> VmInfo:
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
            config.name, self._vm.connection_host(request), request.user, vm_remote_home(request)
        )

    def destroy(self, info: VmInfo) -> None:
        result = self._vm.teardown(self._credentials.model_copy(update={"name": info.name}))
        if not isinstance(result.return_code, int) or result.return_code != 0:
            detail = result.stderr or result.stdout or "VM teardown failed"
            raise RuntimeError(detail.strip())


def multipass_vm_adapter(orchestrator: VmOrchestratorProtocol) -> VmLifecycleAdapter:
    return VmLifecycleAdapter(orchestrator, lifecycle="multipass")


def azure_vm_adapter(
    orchestrator: VmOrchestratorProtocol, *, credentials: VmRequest | None = None
) -> VmLifecycleAdapter:
    return VmLifecycleAdapter(orchestrator, lifecycle="azure", credentials=credentials)


def proxmox_vm_adapter(
    orchestrator: VmOrchestratorProtocol, *, credentials: VmRequest | None = None
) -> VmLifecycleAdapter:
    return VmLifecycleAdapter(orchestrator, lifecycle="proxmox", credentials=credentials)
