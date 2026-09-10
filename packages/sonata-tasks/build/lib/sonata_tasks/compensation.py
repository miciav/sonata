from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sonata_engine import Resource, TaskInputs


def best_effort(error: BaseException, cleanup: Callable[[], object], *, what: str) -> None:
    """Run cleanup without replacing the error that required it."""
    try:
        _ = cleanup()
    except (OSError, RuntimeError) as cleanup_error:
        error.add_note(f"Best-effort {what} after a failed acquire failed: {cleanup_error}")


def compensated_resource[T](
    *,
    title: str,
    acquire: Callable[[TaskInputs], T],
    compensate: Callable[[TaskInputs], object],
    release: Callable[[TaskInputs, T], object] | None = None,
    requires: tuple[Resource[Any], ...] = (),
    always_release: bool = False,
    revive: Callable[[Any], T] | None = None,
) -> Resource[T]:
    """Build a resource whose failed acquire performs best-effort compensation."""

    def undo(inputs: TaskInputs, value: T) -> None:
        _ = compensate(inputs) if release is None else release(inputs, value)

    def acquire_with_compensation(inputs: TaskInputs) -> T:
        try:
            return acquire(inputs)
        except BaseException as error:
            best_effort(error, lambda: compensate(inputs), what=f"compensation for {title}")
            raise

    return Resource(
        title=title,
        acquire=acquire_with_compensation,
        release=undo,
        requires=requires,
        always_release=always_release,
        revive=revive,
    )
