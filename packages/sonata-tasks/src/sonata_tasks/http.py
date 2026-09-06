from __future__ import annotations

from collections.abc import Callable, Mapping

from sonata_engine import Resource

from sonata_tasks.command import Argv, CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

Endpoint = str | Resource[str]


def endpoint_argv(endpoint: Endpoint, build: Callable[[str], tuple[str, ...]]) -> Argv:
    if isinstance(endpoint, str):
        return build(endpoint)
    return lambda inputs: build(inputs.resource(endpoint))


class HttpStatusCheckTask(CommandTask):
    def __init__(
        self,
        *,
        url: Endpoint,
        expected_status: int,
        executor: CommandTaskExecutor,
        role: str = "host",
        payload: str | None = None,
        headers: Mapping[str, str] | None = None,
        options: CommandOptions | None = None,
        title: str | None = None,
        semantic_key: str | None = None,
    ) -> None:
        if not isinstance(url, str) and not semantic_key:
            raise ValueError("semantic_key is required for a dynamic endpoint")

        def build(endpoint: str) -> tuple[str, ...]:
            header_args = tuple(
                arg for item in (headers or {}).items() for arg in ("-H", f"{item[0]}: {item[1]}")
            )
            data = ("--data", payload) if payload is not None else ()
            return (
                "curl",
                "-sS",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                *header_args,
                *data,
                endpoint,
            )

        def check(result: TaskResult) -> None:
            actual = result.stdout.strip()
            if actual != str(expected_status):
                raise RuntimeError(
                    f"{url}: answered {actual or 'nothing'}, expected {expected_status}"
                )

        key = f"http-status:v1:{expected_status}:{semantic_key or url!s}:" + repr(
            (tuple((headers or {}).items()), payload)
        )
        super().__init__(
            title=title or f"Expect {expected_status} from {url}",
            argv=endpoint_argv(url, build),
            executor=executor,
            role=role,
            options=options,
            verify=check,
            semantic_key=key,
        )
