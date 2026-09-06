try:
    from sonata_tasks.vm.providers.proxmox import ProxmoxVmProvider
except ModuleNotFoundError as error:
    if error.name in {"proxmox_sdk", "shellcraft"}:
        raise ModuleNotFoundError(
            "Install sonata-tasks[proxmox] to use ProxmoxVmProvider"
        ) from error
    raise

__all__ = ["ProxmoxVmProvider"]
