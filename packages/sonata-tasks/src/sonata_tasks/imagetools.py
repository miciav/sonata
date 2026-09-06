from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor


def _docker_options(options: CommandOptions | None, docker_config: str) -> CommandOptions:
    current = options or CommandOptions()
    return replace(current, env={**current.env, "DOCKER_CONFIG": docker_config})


class ImagetoolsCreateTask(CommandTask):
    def __init__(
        self,
        *,
        tag: str,
        sources: tuple[str, ...],
        docker_config: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"Create manifest {tag}",
            argv=("docker", "buildx", "imagetools", "create", "--tag", tag, *sources),
            executor=executor,
            role=role,
            options=_docker_options(options, docker_config),
            verify=verify,
            semantic_key=semantic_key,
        )


class ImagetoolsInspectTask(CommandTask):
    def __init__(
        self,
        *,
        reference: str,
        docker_config: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"Inspect manifest {reference}",
            argv=("docker", "buildx", "imagetools", "inspect", reference),
            executor=executor,
            role=role,
            options=_docker_options(options, docker_config),
            verify=verify,
            semantic_key=semantic_key,
        )
