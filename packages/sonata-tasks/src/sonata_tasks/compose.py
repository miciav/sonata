from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sonata_engine import Resource, Steps, TaskInputs

from sonata_tasks.command import CommandTask
from sonata_tasks.compensation import compensated_resource
from sonata_tasks.execution.models import CommandOptions
from sonata_tasks.execution.ports import CommandTaskExecutor


@dataclass(frozen=True, slots=True)
class DockerComposeProject:
    name: str
    file: Path
    ready_url: str
    build: bool = True


class DeployDockerCompose(CommandTask):
    def __init__(
        self,
        project: DockerComposeProject,
        *,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"Deploy Docker Compose project {project.name}",
            argv=(
                "docker",
                "compose",
                "-f",
                str(project.file),
                "-p",
                project.name,
                "up",
                "-d",
                *(("--build",) if project.build else ()),
                "--wait",
            ),
            executor=executor,
            role=role,
            options=options,
        )


class WaitForDockerCompose(CommandTask):
    def __init__(
        self,
        project: DockerComposeProject,
        *,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(
            title=title or f"Wait for Docker Compose project {project.name}",
            argv=(
                "curl",
                "-fsS",
                "--retry",
                "60",
                "--retry-delay",
                "1",
                "--retry-connrefused",
                "--retry-all-errors",
                project.ready_url,
            ),
            executor=executor,
            role=role,
            options=options,
        )


class DestroyDockerCompose(CommandTask):
    def __init__(
        self,
        project: DockerComposeProject,
        *,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
        remove_volumes: bool = False,
        remove_orphans: bool = False,
    ) -> None:
        argv = ["docker", "compose", "-f", str(project.file), "-p", project.name, "down"]
        if remove_volumes:
            argv.append("--volumes")
        if remove_orphans:
            argv.append("--remove-orphans")
        super().__init__(
            title=title or f"Destroy Docker Compose project {project.name}",
            argv=tuple(argv),
            executor=executor,
            role=role,
            options=options,
        )


def docker_compose_resource(
    project: DockerComposeProject,
    *,
    executor: CommandTaskExecutor,
    role: str = "host",
    options: CommandOptions | None = None,
    remove_volumes: bool = False,
    remove_orphans: bool = False,
    requires: tuple[Resource[Any], ...] = (),
) -> Resource[DockerComposeProject]:
    """Manage the supplied project; acquisition performs deploy then readiness."""
    deploy = Steps(
        title=f"Acquire Docker Compose project {project.name}",
        steps=(
            DeployDockerCompose(project, executor=executor, role=role, options=options),
            WaitForDockerCompose(project, executor=executor, role=role, options=options),
        ),
    )
    destroy = DestroyDockerCompose(
        project,
        executor=executor,
        role=role,
        options=options,
        remove_volumes=remove_volumes,
        remove_orphans=remove_orphans,
    )

    def acquire(inputs: TaskInputs) -> DockerComposeProject:
        _ = deploy.run(inputs)
        return project

    return compensated_resource(
        title=f"Acquire Docker Compose project {project.name}",
        acquire=acquire,
        compensate=destroy.run,
        requires=requires,
    )
