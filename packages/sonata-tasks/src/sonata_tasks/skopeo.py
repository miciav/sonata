from __future__ import annotations

from collections.abc import Callable

from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor


class SkopeoCopyTask(CommandTask):
    def __init__(
        self,
        *,
        source: str,
        destination: str,
        authfile: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        src_tls_verify: bool = True,
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        argv = ["skopeo", "copy", "--preserve-digests"]
        if not src_tls_verify:
            argv.append("--src-tls-verify=false")
        argv.extend(("--dest-authfile", authfile, f"docker://{source}", f"docker://{destination}"))
        super().__init__(
            title=title or f"Copy image {source} -> {destination}",
            argv=tuple(argv),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )


class SkopeoInspectTask(CommandTask):
    def __init__(
        self,
        *,
        reference: str,
        authfile: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        tls_verify: bool = True,
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        argv = ["skopeo", "inspect", "--format={{.Digest}}", "--authfile", authfile]
        if not tls_verify:
            argv.append("--tls-verify=false")
        argv.append(f"docker://{reference}")
        super().__init__(
            title=title or f"Inspect {reference}",
            argv=tuple(argv),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )
