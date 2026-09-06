from __future__ import annotations

from collections.abc import Callable
from typing import override

from sonata_engine import Task, TaskInputs, TaskOutcome

from sonata_tasks.core.fingerprint import fingerprint_digest
from sonata_tasks.execution.models import CommandOptions, CommandTaskSpec, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

Argv = tuple[str, ...] | Callable[[TaskInputs], tuple[str, ...]]


class CommandTask(Task[TaskResult]):
    def __init__(
        self,
        *,
        title: str,
        argv: Argv,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
    ) -> None:
        if not title:
            raise ValueError("title must not be empty")
        if not role:
            raise ValueError("role must not be empty")
        if (callable(argv) or verify is not None) and not semantic_key:
            raise ValueError("semantic_key is required for dynamic argv or verification")
        if semantic_key is not None and not semantic_key:
            raise ValueError("semantic_key must not be empty")
        if not callable(argv):
            argv = tuple(argv)
            if not argv or not argv[0] or any(not isinstance(part, str) for part in argv):
                raise ValueError("argv must start with a non-empty program name")

        self.title = title
        self.argv = argv
        self.executor = executor
        self.role = role
        self.options = options or CommandOptions()
        self.verify = verify
        self.semantic_key = semantic_key
        self._binding_key = executor.binding_key(role)
        if not self._binding_key:
            raise ValueError("executor binding_key must not be empty")

    def _spec(self, inputs: TaskInputs) -> CommandTaskSpec:
        argv = self.argv(inputs) if callable(self.argv) else self.argv
        return CommandTaskSpec(
            task_id="",
            summary=self.title,
            argv=argv,
            role=self.role,
            options=self.options,
        )

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[TaskResult]:
        result = self.executor.run(self._spec(inputs))
        expected = self.options.expected_exit_codes
        code_valid = (
            isinstance(result.return_code, int)
            and not isinstance(result.return_code, bool)
            and result.return_code in expected
        )
        if result.status not in ("passed", "failed") or (result.status == "passed") != code_valid:
            raise RuntimeError(
                f"{self.title}: executor returned incoherent status/code "
                f"({result.status!r}, {result.return_code!r})"
            )
        if not code_valid:
            detail = (
                "\n".join(part for part in (result.stderr.strip(), result.stdout.strip()) if part)
                or "no output"
            )
            raise RuntimeError(f"{self.title} failed (exit {result.return_code}): {detail}")
        if self.verify is not None:
            self.verify(result)
        return TaskOutcome(value=result)

    @override
    def _fingerprint_payload(self) -> object:
        argv: object = (
            {"kind": "dynamic", "semantic_key": self.semantic_key}
            if callable(self.argv)
            else {"kind": "static", "value": self.argv}
        )
        return {
            "schema": 1,
            "digest": fingerprint_digest(
                {
                    "contract": 1,
                    "argv": argv,
                    "options": {
                        "cwd": self.options.cwd,
                        "env": self.options.env,
                        "remote_dir": self.options.remote_dir,
                        "expected_exit_codes": self.options.expected_exit_codes,
                        "timeout_seconds": self.options.timeout_seconds,
                    },
                    "role": self.role,
                    "binding_key": self._binding_key,
                    "semantic_key": self.semantic_key,
                }
            ),
        }
