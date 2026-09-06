from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

from sonata_engine import Resource, TaskInputs

from sonata_tasks.compensation import best_effort
from sonata_tasks.transfer import RemoteProvider


def _exec[RequestT](
    provider: RemoteProvider[RequestT], request: RequestT, argv: tuple[str, ...]
) -> str:
    result = provider.exec_argv(request, argv=argv)
    if not isinstance(result.return_code, int) or isinstance(result.return_code, bool):
        raise RuntimeError("remote command returned no integer return_code")
    if result.return_code != 0:
        detail = result.stderr or result.stdout
        raise RuntimeError(
            f"remote command failed (exit {result.return_code})" + (f": {detail}" if detail else "")
        )
    return result.stdout


def _transfer_archive[RequestT](
    provider: RemoteProvider[RequestT], request: RequestT, archive_path: Path, remote_archive: str
) -> None:
    result = provider.transfer_to(request, source=archive_path, destination=remote_archive)
    if not isinstance(result.return_code, int) or isinstance(result.return_code, bool):
        raise RuntimeError("transfer returned no integer return_code")
    if result.return_code != 0:
        detail = result.stderr or result.stdout
        raise RuntimeError(
            f"transfer failed (exit {result.return_code})" + (f": {detail}" if detail else "")
        )


def _verify_remote_archive[RequestT](
    provider: RemoteProvider[RequestT], request: RequestT, remote_archive: str, local_checksum: str
) -> None:
    try:
        remote_checksum = _exec(provider, request, ("sha256sum", remote_archive)).split()[0]
        if remote_checksum != local_checksum:
            raise RuntimeError(
                f"sha256sum mismatch: local={local_checksum}, remote={remote_checksum}"
            )
    except BaseException as error:
        best_effort(
            error,
            lambda: provider.exec_argv(request, argv=("rm", "-f", remote_archive)),
            what="cleanup remote archive after failed verify",
        )
        raise


def _extract_remote_archive[RequestT](
    provider: RemoteProvider[RequestT],
    request: RequestT,
    remote_archive: str,
    remote_source_dir: str,
) -> None:
    try:
        _exec(provider, request, ("mkdir", "-p", remote_source_dir))
        _exec(provider, request, ("tar", "-xf", remote_archive, "-C", remote_source_dir))
    except BaseException as error:
        best_effort(
            error,
            lambda: provider.exec_argv(
                request, argv=("rm", "-rf", remote_source_dir, remote_archive)
            ),
            what="cleanup after failed extract",
        )
        raise


def source_archive_resource[RequestT](
    *,
    repo_root: Path,
    commit: str,
    remote_source_dir: str,
    remote_archive: str,
    provider: RemoteProvider[RequestT],
    request: RequestT,
) -> Resource[str]:
    def acquire(_inputs: TaskInputs) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "source.tar"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "archive",
                    "--format=tar",
                    commit,
                    "-o",
                    str(archive_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            _exec(provider, request, ("mkdir", "-p", str(Path(remote_archive).parent)))
            _transfer_archive(provider, request, archive_path, remote_archive)
            _verify_remote_archive(provider, request, remote_archive, checksum)
            _extract_remote_archive(provider, request, remote_archive, remote_source_dir)
        return remote_source_dir

    def release(_inputs: TaskInputs, _state: str) -> None:
        try:
            provider.exec_argv(request, argv=("rm", "-rf", remote_source_dir))
        except RuntimeError:
            pass

    return Resource(
        title=f"Acquire source archive at {remote_source_dir}", acquire=acquire, release=release
    )
