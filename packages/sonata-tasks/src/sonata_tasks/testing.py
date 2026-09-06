from __future__ import annotations

from dataclasses import dataclass, field

from sonata_tasks.execution.models import CommandTaskSpec, TaskResult


@dataclass
class RecordingExecutor:
    """Record command specs and return queued or default successful results."""

    target_key: str = "recording"
    results: list[TaskResult] = field(default_factory=list)
    seen: list[CommandTaskSpec] = field(default_factory=list)

    def binding_key(self, role: str) -> str:
        return self.target_key

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        self.seen.append(task)
        if self.results:
            return self.results.pop(0)
        return TaskResult(
            task_id=task.task_id,
            status="passed",
            return_code=0,
            expected_exit_codes=task.options.expected_exit_codes,
        )
