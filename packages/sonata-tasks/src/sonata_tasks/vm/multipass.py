try:
    from sonata_tasks.vm.providers.multipass import MultipassVmProvider, resolve_connection_host
except ModuleNotFoundError as error:
    if error.name in {"multipass", "shellcraft"}:
        raise ModuleNotFoundError(
            "Install sonata-tasks[multipass] to use MultipassVmProvider"
        ) from error
    raise


def find_ssh_public_key() -> str:
    """Compatibility hook for callers that patch credential discovery."""
    from multipass import find_ssh_public_key as discover

    return discover()

__all__ = ["MultipassVmProvider", "resolve_connection_host", "find_ssh_public_key"]
