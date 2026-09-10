from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from sonata_tasks.execution.models import CommandTaskSpec, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

__all__ = ["CommandTaskExecutor", "RoleBindings", "RoleBoundCommandTaskExecutor"]


class RoleBindings:
    def __init__(self, executors: Mapping[str, CommandTaskExecutor]) -> None:
        copied = dict(executors)
        if not copied or any(not role for role in copied):
            raise ValueError("executor bindings must have non-empty role names")
        self._executors = MappingProxyType(copied)

    def executor_for(self, role: str) -> CommandTaskExecutor:
        try:
            return self._executors[role]
        except KeyError as error:
            raise ValueError(f"no executor bound for role {role!r}") from error


class RoleBoundCommandTaskExecutor:
    def __init__(self, bindings: RoleBindings) -> None:
        self._bindings = bindings

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        return self._bindings.executor_for(task.role).run(task, dry_run=dry_run)

    def binding_key(self, role: str) -> str:
        return self._bindings.executor_for(role).binding_key(role)
