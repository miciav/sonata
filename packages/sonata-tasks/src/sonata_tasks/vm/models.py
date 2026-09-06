from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, model_validator


@dataclass(frozen=True, slots=True)
class VmConfig:
    name: str
    cpus: int = 2
    memory: str = "2G"
    disk: str = "20G"


@dataclass(frozen=True, slots=True)
class VmInfo:
    name: str
    host: str
    user: str
    home: str


VmLifecycle = Literal["multipass", "external", "azure", "proxmox"]


class VmRequest(BaseModel):
    lifecycle: VmLifecycle
    name: str | None = None
    host: str | None = None
    user: str = "ubuntu"
    home: str | None = None
    cpus: int = 4
    memory: str = "12G"
    disk: str = "30G"
    azure_vm_size: str = "Standard_D4s_v5"
    azure_resource_group: str | None = None
    azure_location: str | None = None
    azure_image_urn: str | None = None
    azure_ssh_key_path: str | None = None
    azure_open_ports: tuple[int, ...] | None = None
    proxmox_host: str | None = None
    proxmox_node: str | None = None
    proxmox_user: str | None = None
    proxmox_password: str | None = None
    proxmox_template_id: int | None = None
    proxmox_ssh_key_path: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle_requirements(self) -> Self:
        if self.lifecycle == "external" and not self.host:
            raise ValueError("host is required for external lifecycle")
        return self


def vm_remote_home(request: VmRequest) -> str:
    if request.home:
        return request.home
    return "/root" if request.user == "root" else f"/home/{request.user}"
