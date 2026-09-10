"""Run Gradle targets through the project's ``gradlew`` wrapper."""

from __future__ import annotations

from collections.abc import Mapping

from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions
from sonata_tasks.execution.ports import CommandTaskExecutor


class GradleTask(CommandTask):
    """Run one or more Gradle targets with the wrapper script."""

    def __init__(
        self,
        *targets: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        properties: Mapping[str, str] | None = None,
        daemon: bool = False,
        options: CommandOptions | None = None,
        title: str | None = None,
    ) -> None:
        """Configure a ``./gradlew <targets>`` invocation.

        Each ``properties`` entry becomes a ``-Pname=value`` flag. ``daemon``
        selects ``--daemon`` over the default ``--no-daemon``. ``title``
        defaults to one naming the targets.

        Raises:
            ValueError: If no targets were given.

        """
        if not targets:
            raise ValueError("a Gradle task needs at least one target")
        property_args = tuple(
            f"-P{name}={value}" for name, value in (properties or {}).items()
        )
        super().__init__(
            title=title or f"Run gradle {' '.join(targets)}",
            argv=(
                "./gradlew",
                *targets,
                *property_args,
                "--daemon" if daemon else "--no-daemon",
            ),
            executor=executor,
            role=role,
            options=options,
        )
