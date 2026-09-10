"""Command tasks for the docker subcommands used by the catalogue."""

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
        """Build ``docker <args>`` as the command to run.

        ``title`` defaults to one echoing the arguments, and ``verify`` and
        ``semantic_key`` are forwarded to
        :class:`~sonata_tasks.command.CommandTask`.
        """
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
    """Build an image from a Dockerfile and tag it."""

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
        """Configure ``docker build`` for ``context``, tagged ``image``.

        ``title`` defaults to one naming the image.
        """
        super().__init__(
            title=title or f"Build image {image}",
            argv=("docker", "build", "-f", dockerfile, "-t", image, context),
            executor=executor,
            role=role,
            options=options,
        )


class DockerPushTask(CommandTask):
    """Push a tagged image to its registry."""

    def __init__(
        self,
        *,
        image: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
    ) -> None:
        """Configure ``docker push`` for ``image``.

        ``title`` defaults to one naming the image.
        """
        super().__init__(
            title=title or f"Push image {image}",
            argv=("docker", "push", image),
            executor=executor,
            role=role,
            options=options,
        )


class DockerInspectTask(CommandTask):
    """Inspect a container and format the result with a Go template."""

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
        """Configure ``docker inspect`` for ``container``.

        ``fmt`` is passed as ``--format``; it defaults to the container's host
        configuration as JSON. ``title`` defaults to one naming the container.
        """
        super().__init__(
            title=title or f"Inspect {container}",
            argv=("docker", "inspect", f"--format={fmt}", container),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )
