"""A stub command executor for tests that need to inspect what would run."""

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
        """Report ``target_key`` for every role, so all commands look local."""
        return self.target_key

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        """Record ``task`` and return the next queued result.

        Returns the next entry of ``results`` when one is queued; once that list
        is exhausted, returns a synthetic passed result carrying the task's own
        id and expected exit codes. ``dry_run`` is accepted for interface
        compatibility and does not change what is returned.
        """
        self.seen.append(task)
        if self.results:
            return self.results.pop(0)
        return TaskResult(
            task_id=task.task_id,
            status="passed",
            return_code=0,
            expected_exit_codes=task.options.expected_exit_codes,
        )
