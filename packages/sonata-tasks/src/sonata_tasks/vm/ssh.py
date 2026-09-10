"""Shared SSH helpers for the VM providers."""

from __future__ import annotations

import shlex
from pathlib import Path


def find_ssh_private_key_path(public_key: str | None = None) -> Path | None:
    """Locate a private key in ``~/.ssh`` that has a matching ``.pub`` file.

    When ``public_key`` is given, only a pair whose public half matches it is
    accepted. Returns the private key path, or ``None`` when no usable pair is
    found.
    """
    ssh_dir = Path.home() / ".ssh"
    normalized = public_key.strip() if public_key else None
    for name in ("id_ed25519", "id_rsa", "id_ecdsa", "id_dsa"):
        public = ssh_dir / f"{name}.pub"
        private = ssh_dir / name
        if public.exists() and private.exists():
            if (
                normalized is not None
                and public.read_text(encoding="utf-8").strip() != normalized
            ):
                continue
            return private
    return None


def ssh_command(
    *, private_key_path: Path | None = None, port: int | None = None
) -> str:
    """Build a shell-quoted ``ssh`` invocation with host checking disabled.

    ``port`` and ``private_key_path`` are added as ``-p`` and ``-i`` options when
    supplied.
    """
    parts = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
    ]
    if port is not None:
        parts.extend(("-p", str(port)))
    if private_key_path is not None:
        parts.extend(("-i", str(private_key_path)))
    return shlex.join(parts)
