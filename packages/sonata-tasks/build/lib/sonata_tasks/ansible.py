from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions
from sonata_tasks.execution.ports import CommandTaskExecutor


def build_ansible_argv(
    *,
    playbook: Path,
    inventory: str,
    user: str,
    private_key_path: Path | None = None,
    extra_vars: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    argv = ["ansible-playbook", "-i", inventory, "-u", user]
    if private_key_path is not None:
        argv.extend(("--private-key", str(private_key_path)))
    for key, value in (extra_vars or {}).items():
        argv.extend(("-e", f"{key}={value}"))
    argv.append(str(Path(playbook).absolute()))
    return tuple(argv)


class AnsiblePlaybookTask(CommandTask):
    def __init__(
        self,
        *,
        playbook: Path,
        inventory: str,
        user: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        private_key_path: Path | None = None,
        extra_vars: Mapping[str, str] | None = None,
        ansible_config: Path | None = None,
        options: CommandOptions | None = None,
        title: str | None = None,
    ) -> None:
        current = options or CommandOptions()
        if ansible_config is not None:
            current = replace(current, env={**current.env, "ANSIBLE_CONFIG": str(ansible_config)})
        super().__init__(
            title=title or f"Run Ansible playbook {Path(playbook).name}",
            argv=build_ansible_argv(
                playbook=playbook,
                inventory=inventory,
                user=user,
                private_key_path=private_key_path,
                extra_vars=extra_vars,
            ),
            executor=executor,
            role=role,
            options=current,
        )
