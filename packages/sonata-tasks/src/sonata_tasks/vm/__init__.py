from __future__ import annotations

from sonata_engine import Resource, TaskInputs

from sonata_tasks.compensation import compensated_resource
from sonata_tasks.vm.models import VmConfig, VmInfo
from sonata_tasks.vm.ports import VmLifecycleProtocol
from sonata_tasks.vm.tasks import DestroyVm, EnsureVmRunning

__all__ = ["vm_resource"]


def vm_resource(
    *,
    title: str,
    lifecycle: VmLifecycleProtocol,
    config: VmConfig,
    fallback_info: VmInfo,
    external: bool = False,
) -> Resource[VmInfo]:
    ensure = EnsureVmRunning(f"{config.name}-ensure", title, lifecycle, config)

    def destroy(info: VmInfo) -> None:
        DestroyVm(f"{info.name}-destroy", f"Destroy {info.name}", lifecycle, info).run()

    def acquire(_inputs: TaskInputs) -> VmInfo:
        return ensure.run()

    def compensate(_inputs: TaskInputs) -> None:
        if not external:
            destroy(fallback_info)

    def release(_inputs: TaskInputs, info: VmInfo) -> None:
        if not external:
            destroy(info)

    return compensated_resource(
        title=title,
        acquire=acquire,
        compensate=compensate,
        release=release,
        revive=lambda raw: VmInfo(**raw),
    )
