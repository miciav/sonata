"""Check an HTTP endpoint's status code as a command task."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sonata_engine import Resource

from sonata_tasks.command import Argv, CommandTask
from sonata_tasks.core.fingerprint import semantic_key as build_semantic_key
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

Endpoint = str | Resource[str]


def endpoint_argv(endpoint: Endpoint, build: Callable[[str], tuple[str, ...]]) -> Argv:
    """Turn an endpoint and an argv builder into a :data:`Argv`.

    A plain string is resolved immediately; a resource defers to run time, when
    the resolved URL is read from the task inputs and passed to ``build``.
    """
    if isinstance(endpoint, str):
        return build(endpoint)
    return lambda inputs: build(inputs.resource(endpoint))


class HttpStatusCheckTask(CommandTask):
    """Request a URL with curl and assert the status code it answers with."""

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
        """Configure the request and the status check that follows it.

        ``payload``, when given, is sent with ``--data`` and each ``headers``
        entry becomes a ``-H`` flag. The verification fails unless the response
        code equals ``expected_status``. Because the fingerprint is derived from
        the fields above rather than the argv, a non-string ``url`` must come
        with its own ``semantic_key``. ``title`` defaults to one naming the
        expected status and endpoint.

        Raises:
            ValueError: If ``url`` is a resource and no ``semantic_key`` was
                supplied.

        """
        if not isinstance(url, str) and not semantic_key:
            raise ValueError("semantic_key is required for a dynamic endpoint")

        def build(endpoint: str) -> tuple[str, ...]:
            header_args = tuple(
                arg
                for item in (headers or {}).items()
                for arg in ("-H", f"{item[0]}: {item[1]}")
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

        key = build_semantic_key(
            "http-status:v2",
            {
                "endpoint": semantic_key or url,
                "expected_status": expected_status,
                "headers": dict(headers or {}),
                "payload": payload,
            },
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
