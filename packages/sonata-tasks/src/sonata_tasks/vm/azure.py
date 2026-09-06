try:
    from sonata_tasks.vm.providers.azure import AzureVmFacts, AzureVmProvider
except ModuleNotFoundError as error:
    if error.name in {"azure_vm", "shellcraft"}:
        raise ModuleNotFoundError("Install sonata-tasks[azure] to use AzureVmProvider") from error
    raise

__all__ = ["AzureVmFacts", "AzureVmProvider"]
