"""Executable, product-independent consumer of the sonata-tasks wheel."""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from sonata_tasks import CommandOptions, CommandTask
from sonata_tasks.docker import DockerBuildTask, DockerInspectTask
from sonata_tasks.execution.bindings import RoleBindings, RoleBoundCommandTaskExecutor
from sonata_tasks.execution.local import LocalCommandTaskExecutor
from sonata_tasks.k6 import K6Task
from sonata_tasks.k6_models import K6Config
from sonata_tasks.testing import RecordingExecutor

from sonata_engine import Workflow


def main() -> None:
    recording = RecordingExecutor(target_key="example-builder")
    routed = RoleBoundCommandTaskExecutor(RoleBindings({"builder": recording}))
    options = CommandOptions(timeout_seconds=600)
    workflow = Workflow("shared-task-client")
    workflow.add(
        DockerBuildTask(
            image="example/app:dev",
            dockerfile="Dockerfile",
            context=".",
            executor=routed,
            role="builder",
            options=options,
        )
    )
    workflow.add(
        DockerInspectTask(
            container="example-app",
            executor=routed,
            role="builder",
            options=options,
        )
    )
    workflow.add(
        K6Task(
            K6Config(
                Path("load.js"), Path("summary.json"), env={"TARGET_URL": "https://example.test"}
            ),
            executor=routed,
            role="builder",
        )
    )
    result = workflow.run()

    assert len(result.tasks) == 3
    assert recording.seen[0].argv[:2] == ("docker", "build")
    assert recording.seen[1].argv[:2] == ("docker", "inspect")
    assert recording.seen[0].options.timeout_seconds == 600
    assert recording.binding_key("builder") == "example-builder"
    assert "NANOFAAS_URL" not in " ".join(recording.seen[2].argv)

    with TemporaryDirectory() as directory:
        local = Workflow("local-command")
        local.add(
            CommandTask(
                title="Run an innocent local command",
                argv=(
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('ok').write_text('ok')",
                ),
                executor=LocalCommandTaskExecutor(),
                options=CommandOptions(cwd=Path(directory)),
            )
        )
        local.run()
        assert (Path(directory) / "ok").read_text() == "ok"


if __name__ == "__main__":
    main()
