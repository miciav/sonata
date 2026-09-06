from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sonata_engine import Resource, TaskInputs

from sonata_tasks.compensation import best_effort
from sonata_tasks.transfer import RemoteCommandProvider


def registry_tunnel_resource[RequestT](
    *,
    registry_upstream: str | Callable[[], str],
    provider: RemoteCommandProvider[RequestT],
    request: RequestT,
    unit_name: str,
    listen_port: int,
    upstream_port: int,
    title: str | None = None,
    requires: tuple[Resource[Any], ...] = (),
) -> Resource[None]:
    if not unit_name:
        raise ValueError("unit_name must not be empty")
    stop = ("sudo", "systemctl", "stop", unit_name)
    reset = ("sudo", "systemctl", "reset-failed", unit_name)

    def run(argv: tuple[str, ...], *, action: str, absent_ok: bool = False) -> None:
        result = provider.exec_argv(request, argv)
        if not isinstance(result.return_code, int) or isinstance(result.return_code, bool):
            raise RuntimeError(f"registry tunnel {action} returned no integer return_code")
        if result.return_code != 0:
            detail = result.stderr or result.stdout
            if absent_ok and any(
                marker in detail.lower() for marker in ("not loaded", "not found", "does not exist")
            ):
                return
            raise RuntimeError(
                f"registry tunnel {action} failed (exit {result.return_code})"
                + (f": {detail}" if detail else "")
            )

    def quiet(argv: tuple[str, ...]) -> None:
        try:
            _ = provider.exec_argv(request, argv)
        except RuntimeError:
            pass

    def stop_and_reset() -> None:
        quiet(stop)
        quiet(reset)

    def acquire(_inputs: TaskInputs) -> None:
        try:
            run(stop, action="acquire", absent_ok=True)
            run(reset, action="acquire", absent_ok=True)
            upstream = registry_upstream() if callable(registry_upstream) else registry_upstream
            run(
                (
                    "sudo",
                    "systemd-run",
                    "--unit",
                    unit_name,
                    "socat",
                    f"TCP-LISTEN:{listen_port},fork,reuseaddr",
                    f"TCP:{upstream}:{upstream_port}",
                ),
                action="acquire",
            )
        except BaseException as error:
            best_effort(error, stop_and_reset, what="registry tunnel failed acquire")
            raise

    def release(_inputs: TaskInputs, _state: None) -> None:
        try:
            run(stop, action="release", absent_ok=True)
        finally:
            quiet(reset)

    resource_title = title or (
        "Acquire registry tunnel"
        if callable(registry_upstream)
        else f"Acquire registry tunnel to {registry_upstream}:{upstream_port}"
    )
    return Resource(
        title=resource_title,
        acquire=acquire,
        release=release,
        requires=requires,
        always_release=True,
    )
