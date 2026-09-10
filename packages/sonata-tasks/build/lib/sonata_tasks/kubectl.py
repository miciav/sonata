from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import override

from sonata_engine import Task, TaskInputs, TaskOutcome
from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor


class KubectlTask(CommandTask):
    def __init__(
        self,
        *args: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        namespace: str | None = None,
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        scope = ("-n", namespace) if namespace is not None else ()
        super().__init__(
            title=title or f"kubectl {' '.join(args)}",
            argv=("kubectl", *scope, *args),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )


class ClusterIpEndpointTask(Task[str]):
    def __init__(self, *, service: str, port: int, title: str | None = None) -> None:
        self.title = title or f"Resolve where {service} answers"
        self._service = service
        self._port = port

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        result = inputs.upstream()
        if not isinstance(result, TaskResult):
            raise RuntimeError(
                f"{self.title}: expected the previous step's command result, got "
                f"{type(result).__name__}"
            )
        address = result.stdout.strip()
        if not address:
            raise RuntimeError(f"service {self._service} reported no ClusterIP")
        return TaskOutcome(value=f"http://{address}:{self._port}")  # NOSONAR

    @override
    def _fingerprint_payload(self) -> object:
        return {"service": self._service, "port": self._port}


def k8s_deployment_readiness(
    *,
    deployment: str,
    namespace: str,
    executor: CommandTaskExecutor,
    role: str = "host",
    timeout_seconds: int = 120,
    options: CommandOptions | None = None,
) -> tuple[CommandTask, CommandTask]:
    attempts = max(1, timeout_seconds // 2)
    appeared = (
        f"for _ in $(seq 1 {attempts}); do "
        f"kubectl -n {shlex.quote(namespace)} get deployment/{shlex.quote(deployment)} "
        ">/dev/null 2>&1 && exit 0; sleep 2; done; "
        "echo "
        f"{shlex.quote(f'deployment {deployment} did not appear within {timeout_seconds}s')} "
        ">&2; "
        "exit 1"
    )
    return (
        CommandTask(
            title=f"Wait for deployment/{deployment}",
            argv=("bash", "-lc", appeared),
            executor=executor,
            role=role,
            options=options,
        ),
        KubectlTask(
            "rollout",
            "status",
            f"deployment/{deployment}",
            f"--timeout={timeout_seconds}s",
            executor=executor,
            role=role,
            namespace=namespace,
            title=f"Roll out deployment/{deployment}",
            options=options,
        ),
    )
