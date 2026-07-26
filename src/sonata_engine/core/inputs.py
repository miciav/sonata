from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar, cast

from sonata_engine.errors import ResourceUnavailableError, UndeclaredResourceError

if TYPE_CHECKING:
    from sonata_engine.core.resource_task import Resource

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class TaskInputs:
    """The resource values a task is permitted to observe during one run."""

    _values: Mapping[Resource[Any], object]
    _accessible: Set[Resource[Any]]

    @classmethod
    def empty(cls) -> TaskInputs:
        return cls(MappingProxyType({}), frozenset())

    @classmethod
    def _for_resources(
        cls, values: Mapping[Resource[Any], object], accessible: Set[Resource[Any]]
    ) -> TaskInputs:
        return cls(MappingProxyType(dict(values)), frozenset(accessible))

    def resource(self, resource: Resource[T]) -> T:
        if resource not in self._accessible:
            raise UndeclaredResourceError(f"resource {resource.title!r} is not declared")
        try:
            value = self._values[resource]  # type: ignore[index]
        except KeyError as exc:
            raise ResourceUnavailableError(f"resource {resource.title!r} is unavailable") from exc
        return cast(T, value)
