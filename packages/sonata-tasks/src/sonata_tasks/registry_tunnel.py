from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sonata_engine import Resource, TaskInputs
from sonata_tasks.compensation import best_effort
from sonata_tasks.transfer import RemoteCommandProvider, RemoteOperationResult


def _require_tunnel_result(
    result: RemoteOperationResult,
    *,
    action: str,
    absent_ok: bool,
) -> None:
    return_code = result.return_code
    if not isinstance(return_code, int) or isinstance(return_code, bool):
        raise RuntimeError(f"registry tunnel {action} returned no integer return_code")
    if return_code == 0:
        return
    detail = result.stderr or result.stdout
    absent_markers = ("not loaded", "not found", "does not exist")
    if absent_ok and any(marker in detail.lower() for marker in absent_markers):
        return
    suffix = f": {detail}" if detail else ""
    raise RuntimeError(f"registry tunnel {action} failed (exit {return_code}){suffix}")


def _tunnel_title(
    title: str | None,
    registry_upstream: str | Callable[[], str],
    upstream_port: int,
) -> str:
    if title is not None:
        return title
    if callable(registry_upstream):
        return "Acquire registry tunnel"
    return f"Acquire registry tunnel to {registry_upstream}:{upstream_port}"


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
        _require_tunnel_result(result, action=action, absent_ok=absent_ok)

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

    return Resource(
        title=_tunnel_title(title, registry_upstream, upstream_port),
        acquire=acquire,
        release=release,
        requires=requires,
        always_release=True,
    )
