"""Dispatch command tasks to the executor bound to their role."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from sonata_tasks.execution.models import CommandTaskSpec, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

__all__ = ["CommandTaskExecutor", "RoleBindings", "RoleBoundCommandTaskExecutor"]


class RoleBindings:
    """An immutable map from role name to the executor that serves it."""

    def __init__(self, executors: Mapping[str, CommandTaskExecutor]) -> None:
        """Copy ``executors`` into a frozen mapping.

        Raises:
            ValueError: If ``executors`` is empty or any role name is empty.

        """
        copied = dict(executors)
        if not copied or any(not role for role in copied):
            raise ValueError("executor bindings must have non-empty role names")
        self._executors = MappingProxyType(copied)

    def executor_for(self, role: str) -> CommandTaskExecutor:
        """Return the executor bound to ``role``.

        Raises:
            ValueError: If no executor is bound to ``role``.

        """
        try:
            return self._executors[role]
        except KeyError as error:
            raise ValueError(f"no executor bound for role {role!r}") from error


class RoleBoundCommandTaskExecutor:
    """Routes each task to the executor bound to that task's role."""

    def __init__(self, bindings: RoleBindings) -> None:
        """Store the ``bindings`` used to resolve task roles."""
        self._bindings = bindings

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        """Run ``task`` on the executor bound to ``task.role``."""
        return self._bindings.executor_for(task.role).run(task, dry_run=dry_run)

    def binding_key(self, role: str) -> str:
        """Return the binding key of the executor bound to ``role``."""
        return self._bindings.executor_for(role).binding_key(role)
