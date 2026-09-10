from sonata_tasks import CommandOptions, CommandTask, CommandTaskSpec, TaskResult


def test_root_api_exposes_the_stable_command_contract() -> None:
    assert CommandTask.__name__ == "CommandTask"
    assert CommandOptions.__name__ == "CommandOptions"
    assert CommandTaskSpec.__name__ == "CommandTaskSpec"
    assert TaskResult.__name__ == "TaskResult"


def test_root_import_does_not_eagerly_import_optional_dependencies() -> None:
    import subprocess
    import sys

    code = (
        "import sonata_tasks,sys; "
        "assert not "
        "{'httpx','azure_vm','multipass','proxmox_sdk'}.intersection(sys.modules)"
    )
    subprocess.run((sys.executable, "-c", code), check=True)
