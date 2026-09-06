from __future__ import annotations

from sonata_tasks.errors import UnsupportedCommandOptionError
from sonata_tasks.execution.models import CommandTaskSpec, TaskResult
from sonata_tasks.execution.ports import HostCommandRunner, VmCommandRunner


def _result(task: CommandTaskSpec, return_code: int, stdout: str, stderr: str) -> TaskResult:
    expected = task.options.expected_exit_codes
    return TaskResult(
        task_id=task.task_id,
        status="passed" if return_code in expected else "failed",
        return_code=return_code,
        expected_exit_codes=expected,
        stdout=stdout,
        stderr=stderr,
    )


class HostCommandTaskExecutor:
    def __init__(self, runner: HostCommandRunner, *, target_key: str = "local") -> None:
        if not target_key:
            raise ValueError("target_key must not be empty")
        self._runner = runner
        self._target_key = target_key

    def binding_key(self, role: str) -> str:
        return self._target_key

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        options = task.options
        if options.remote_dir is not None:
            raise UnsupportedCommandOptionError("the host executor does not support remote_dir")
        if options.timeout_seconds is not None:
            raise UnsupportedCommandOptionError(
                "the injected host runner does not support timeout_seconds"
            )
        result = self._runner.run(
            list(task.argv), cwd=options.cwd, env=dict(options.env), dry_run=dry_run
        )
        return _result(task, result.return_code, result.stdout, result.stderr)


class VmCommandTaskExecutor:
    def __init__(self, runner: VmCommandRunner, *, target_key: str) -> None:
        if not target_key:
            raise ValueError("target_key must not be empty")
        self._runner = runner
        self._target_key = target_key

    def binding_key(self, role: str) -> str:
        return self._target_key

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        options = task.options
        if options.cwd is not None:
            raise UnsupportedCommandOptionError("the VM executor does not support local cwd")
        if options.timeout_seconds is not None:
            raise UnsupportedCommandOptionError(
                "the injected VM runner does not support timeout_seconds"
            )
        result = self._runner.run_vm_command(
            task.argv,
            env=dict(options.env),
            remote_dir=options.remote_dir,
            dry_run=dry_run,
        )
        return _result(task, result.return_code, result.stdout, result.stderr)
