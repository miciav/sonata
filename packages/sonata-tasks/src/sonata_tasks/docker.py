from __future__ import annotations

from collections.abc import Callable

from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor


class DockerTask(CommandTask):
    """Run one Docker subcommand."""

    def __init__(
        self,
        *args: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"docker {' '.join(args)}",
            argv=("docker", *args),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )


class DockerBuildTask(CommandTask):
    def __init__(
        self,
        *,
        image: str,
        dockerfile: str,
        context: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"Build image {image}",
            argv=("docker", "build", "-f", dockerfile, "-t", image, context),
            executor=executor,
            role=role,
            options=options,
        )


class DockerPushTask(CommandTask):
    def __init__(
        self,
        *,
        image: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"Push image {image}",
            argv=("docker", "push", image),
            executor=executor,
            role=role,
            options=options,
        )


class DockerInspectTask(CommandTask):
    def __init__(
        self,
        *,
        container: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        fmt: str = "{{json .HostConfig}}",
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"Inspect {container}",
            argv=("docker", "inspect", f"--format={fmt}", container),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )
