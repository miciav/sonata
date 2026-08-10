from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WorkflowCompletion:
    workflow_id: str
    started_at: datetime
    finished_at: datetime
    error: BaseException | None


class WorkflowObserver(Protocol):
    def finished(self, completion: WorkflowCompletion) -> None: ...
