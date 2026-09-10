from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sonata_tasks.transfer import RemoteOperationResult
from sonata_tasks.vm.models import VmConfig, VmInfo, VmRequest


class VmLifecycleProtocol(Protocol):
    def ensure_running(self, config: VmConfig) -> VmInfo: ...
    def destroy(self, info: VmInfo) -> None: ...


class VmOrchestratorProtocol(Protocol):
    def ensure_running(self, request: VmRequest) -> RemoteOperationResult: ...
    def connection_host(self, request: VmRequest) -> str: ...
    def teardown(self, request: VmRequest) -> RemoteOperationResult: ...


class VmCommandProvider(Protocol):
    def exec_argv(
        self,
        request: VmRequest,
        argv: tuple[str, ...] | list[str],
        *,
        env: dict[str, str] | None = None,
        remote_dir: str | None = None,
        dry_run: bool = False,
    ) -> RemoteOperationResult: ...
    def transfer_to(
        self, request: VmRequest, *, source: Path, destination: str
    ) -> RemoteOperationResult: ...
    def transfer_from(
        self, request: VmRequest, *, source: str, destination: Path
    ) -> RemoteOperationResult: ...
