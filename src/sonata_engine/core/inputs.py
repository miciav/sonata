from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar, cast

from sonata_engine.errors import (
    NoUpstreamValueError,
    ResourceUnavailableError,
    UndeclaredResourceError,
)

if TYPE_CHECKING:
    from sonata_engine.core.resource_task import Resource

T = TypeVar("T")

# Distinct from `None`, which is a legitimate and reconstructible step value.
_NO_UPSTREAM: Any = object()


@dataclass(frozen=True, slots=True)
class TaskInputs:
    """The resource values a task is permitted to observe during one run."""

    _values: Mapping[int, object]
    _accessible: Set[int]
    _upstream: Any = _NO_UPSTREAM

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))
        object.__setattr__(self, "_accessible", frozenset(self._accessible))

    @classmethod
    def empty(cls) -> TaskInputs:
        return cls({}, frozenset())

    @classmethod
    def _for_resources(
        cls, values: Mapping[Resource[Any], object], accessible: Set[Resource[Any]]
    ) -> TaskInputs:
        return cls._for_resource_values(
            {id(resource): value for resource, value in values.items()}, accessible
        )

    @classmethod
    def _for_resource_values(
        cls, values: Mapping[int, object], accessible: Iterable[Resource[Any]]
    ) -> TaskInputs:
        return cls(values, frozenset(map(id, accessible)))

    def resource(self, resource: Resource[T]) -> T:
        resource_id = id(resource)
        if resource_id not in self._accessible:
            raise UndeclaredResourceError(f"resource {resource.title!r} is not declared")
        try:
            value = self._values[resource_id]
        except KeyError as exc:
            raise ResourceUnavailableError(f"resource {resource.title!r} is unavailable") from exc
        return cast(T, value)

    def upstream(self) -> Any:  # noqa: ANN401
        """The value the preceding step produced.

        Raises when nothing precedes this step: the first step of a top-level
        composite, or a task not running inside one.
        """
        if self._upstream is _NO_UPSTREAM:
            raise NoUpstreamValueError("no upstream value: nothing ran before this step")
        return self._upstream
