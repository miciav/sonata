from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

SYFT_IMAGE = "anchore/syft@sha256:f94e5d9fce1f2278491a8e3a63bd5f6ddb81fdfdbb8bf7a1637565c1d5344357"


class SyftTask(CommandTask):
    def __init__(
        self,
        *,
        image: str,
        output_path: str,
        docker_config: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        tool_image: str = SYFT_IMAGE,
        output_format: str = "spdx-json",
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        output = Path(output_path)
        super().__init__(
            title=title or f"Syft SBOM {image}",
            argv=(
                "docker",
                "run",
                "--rm",
                "--env",
                "DOCKER_CONFIG=/auth",
                "--volume",
                f"{docker_config}:/auth:ro",
                "--volume",
                f"{output.parent}:/out",
                tool_image,
                image,
                "-o",
                f"{output_format}=/out/{output.name}",
            ),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )
