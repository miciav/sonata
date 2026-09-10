"""What a task is handed when it runs.

A `TaskInputs` carries the acquired resource values a task declared, plus the
value the preceding step produced when the task sits inside a composite. Both
are reads through a running workflow: a task cannot obtain a resource it never
declared, and the container is frozen once built.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from sonata_engine.errors import (
    NoUpstreamValueError,
    ResourceUnavailableError,
    UndeclaredResourceError,
)

if TYPE_CHECKING:
    from sonata_engine.core.resource_task import Resource
    from sonata_engine.core.step_scope import _StepScopeProtocol

# Distinct from `None`, which is a legitimate and reconstructible step value.
_NO_UPSTREAM: Any = object()


@dataclass(frozen=True, slots=True)
class TaskInputs:
    """The resource values a task is permitted to observe during one run."""

    _values: Mapping[int, object]
    _accessible: Set[int]
    _upstream: Any = _NO_UPSTREAM
    # compare=False is load-bearing, not cosmetic: `_StepScope` holds a `TaskInputs`
    # in its own `base_inputs` field, so if this field participated in `__eq__`,
    # comparing two `TaskInputs` would walk into a scope and back into inputs.
    _step_scope: _StepScopeProtocol | None = field(
        default=None, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        """Freeze the resource-value mapping and the accessible-id set."""
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))
        object.__setattr__(self, "_accessible", frozenset(self._accessible))

    @classmethod
    def empty(cls) -> TaskInputs:
        """Return inputs that grant access to no resource and have no upstream."""
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

    def resource[T](self, resource: Resource[T]) -> T:
        """Return the acquired value of a declared `Resource`, typed as its value.

        Raises `UndeclaredResourceError` if this task never declared the
        resource, or `ResourceUnavailableError` if it declared it but no value
        was published for it.
        """
        resource_id = id(resource)
        if resource_id not in self._accessible:
            raise UndeclaredResourceError(
                f"resource {resource.title!r} is not declared"
            )
        try:
            value = self._values[resource_id]
        except KeyError as exc:
            raise ResourceUnavailableError(
                f"resource {resource.title!r} is unavailable"
            ) from exc
        return cast(T, value)

    def upstream(self) -> Any:  # noqa: ANN401
        """Return the value the preceding step produced.

        Raises when nothing precedes this step: the first step of a top-level
        composite, or a task not running inside one.
        """
        if self._upstream is _NO_UPSTREAM:
            raise NoUpstreamValueError(
                "no upstream value: nothing ran before this step"
            )
        return self._upstream
