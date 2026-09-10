"""Turn already-resolved command specs into a runnable step sequence."""

from __future__ import annotations

from collections.abc import Sequence

from sonata_engine import Steps

from sonata_tasks.command import CommandTask
from sonata_tasks.core.fingerprint import fingerprint_digest
from sonata_tasks.execution.models import CommandTaskSpec
from sonata_tasks.execution.ports import CommandTaskExecutor


def command_specs_composite(
    commands: Sequence[CommandTaskSpec], executor: CommandTaskExecutor, *, title: str
) -> Steps:
    """Wrap each spec in a :class:`CommandTask` and collect them as ``Steps``.

    ``title`` labels the composite; a spec without a summary is titled by its
    position in ``commands``.
    """
    return Steps(
        title=title,
        steps=tuple(
            CommandTask(
                title=spec.summary or f"Command {index}",
                argv=spec.argv,
                executor=executor,
                role=spec.role,
                options=spec.options,
            )
            for index, spec in enumerate(commands)
        ),
    )


def command_specs_fingerprint(
    commands: Sequence[CommandTaskSpec], executor: CommandTaskExecutor
) -> str:
    """Hash the same static command inputs used by CommandTask fingerprints."""
    return fingerprint_digest(
        {
            "commands": tuple(
                {
                    "argv": spec.argv,
                    "role": spec.role,
                    "binding_key": executor.binding_key(spec.role),
                    "options": {
                        "cwd": spec.options.cwd,
                        "env": spec.options.env,
                        "remote_dir": spec.options.remote_dir,
                        "expected_exit_codes": spec.options.expected_exit_codes,
                        "timeout_seconds": spec.options.timeout_seconds,
                    },
                }
                for spec in commands
            )
        }
    )
