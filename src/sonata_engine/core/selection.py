from __future__ import annotations

from dataclasses import dataclass

from sonata_engine.errors import SelectionError


@dataclass(frozen=True, slots=True)
class Selection:
    """Which consumer tasks survive compilation, addressed by title slug.

    Selection deliberately names tasks by slug rather than by compiled
    `task_id`: ordinals renumber over the survivors, so an ID is not a stable
    handle for the very operation that changes it.

    Only consumer tasks are selectable. Acquire/release units are engine-owned
    and are re-spliced around whichever consumers survive, so a slice keeps its
    cleanup without the caller arranging anything.
    """

    only: str | None = None
    start: str | None = None
    until: str | None = None

    def __post_init__(self) -> None:
        if self.only is not None and (self.start is not None or self.until is not None):
            raise SelectionError("only is mutually exclusive with start and until")

    @property
    def is_empty(self) -> bool:
        """True when this selection filters nothing."""
        return self.only is None and self.start is None and self.until is None
