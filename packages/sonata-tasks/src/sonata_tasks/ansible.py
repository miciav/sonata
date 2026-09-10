"""Run an Ansible playbook as a command task."""

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
    """Build the ``ansible-playbook`` argv for a playbook run.

    ``private_key_path`` becomes ``--private-key`` and each ``extra_vars`` entry
    becomes an ``-e key=value`` pair. The playbook is appended last as an
    absolute path, so the command does not depend on the caller's working
    directory.
    """
    argv = ["ansible-playbook", "-i", inventory, "-u", user]
    if private_key_path is not None:
        argv.extend(("--private-key", str(private_key_path)))
    for key, value in (extra_vars or {}).items():
        argv.extend(("-e", f"{key}={value}"))
    argv.append(str(Path(playbook).absolute()))
    return tuple(argv)


class AnsiblePlaybookTask(CommandTask):
    """Run a playbook against an inventory, optionally under a custom config.

    Command building is left to :func:`build_ansible_argv`; this class only
    folds ``ansible_config`` into the environment as ``ANSIBLE_CONFIG`` before
    handing everything to :class:`~sonata_tasks.command.CommandTask`.
    """

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
        """Configure the playbook run.

        ``ansible_config`` is exported as ``ANSIBLE_CONFIG`` on top of any
        environment already set in ``options``. ``title`` defaults to the
        playbook's file name, and ``role`` selects the executor binding.
        """
        current = options or CommandOptions()
        if ansible_config is not None:
            current = replace(
                current, env={**current.env, "ANSIBLE_CONFIG": str(ansible_config)}
            )
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
