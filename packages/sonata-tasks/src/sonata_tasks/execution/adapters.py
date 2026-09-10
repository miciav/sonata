"""Executors that delegate commands to an injected host or VM runner."""

from __future__ import annotations

from sonata_tasks.errors import UnsupportedCommandOptionError
from sonata_tasks.execution.models import CommandTaskSpec, TaskResult
from sonata_tasks.execution.ports import HostCommandRunner, VmCommandRunner


def _result(
    task: CommandTaskSpec, return_code: int, stdout: str, stderr: str
) -> TaskResult:
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
    """Runs host commands through an injected runner and scores the result.

    Options the runner cannot honour are rejected before it is called, so an
    unsupported ``remote_dir`` or ``timeout_seconds`` fails without side
    effects.
    """

    def __init__(self, runner: HostCommandRunner, *, target_key: str = "local") -> None:
        """Store ``runner`` and the binding key reported for every role.

        Raises:
            ValueError: If ``target_key`` is empty.

        """
        if not target_key:
            raise ValueError("target_key must not be empty")
        self._runner = runner
        self._target_key = target_key

    def binding_key(self, role: str) -> str:
        """Return the configured target key, ignoring ``role``.

        All tasks on a host executor share one destination, so there is
        nothing for the role to change.
        """
        del role
        return self._target_key

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        """Run ``task`` on the host and classify its exit code.

        Raises:
            UnsupportedCommandOptionError: If ``remote_dir`` or
                ``timeout_seconds`` is set, which the host runner cannot
                honour.

        """
        options = task.options
        if options.remote_dir is not None:
            raise UnsupportedCommandOptionError(
                "the host executor does not support remote_dir"
            )
        if options.timeout_seconds is not None:
            raise UnsupportedCommandOptionError(
                "the injected host runner does not support timeout_seconds"
            )
        result = self._runner.run(
            list(task.argv), cwd=options.cwd, env=dict(options.env), dry_run=dry_run
        )
        return _result(task, result.return_code, result.stdout, result.stderr)


class VmCommandTaskExecutor:
    """Runs VM commands through an injected runner and scores the result.

    Local ``cwd`` and ``timeout_seconds`` are rejected before the runner is
    called, because it cannot honour them.
    """

    def __init__(self, runner: VmCommandRunner, *, target_key: str) -> None:
        """Store ``runner`` and the binding key reported for every role.

        Raises:
            ValueError: If ``target_key`` is empty.

        """
        if not target_key:
            raise ValueError("target_key must not be empty")
        self._runner = runner
        self._target_key = target_key

    def binding_key(self, role: str) -> str:
        """Return the configured target key, ignoring ``role``.

        All tasks on a VM executor share one destination, so there is nothing
        for the role to change.
        """
        del role
        return self._target_key

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        """Run ``task`` in the target VM and classify its exit code.

        Raises:
            UnsupportedCommandOptionError: If ``cwd`` or ``timeout_seconds``
                is set, which the VM runner cannot honour.

        """
        options = task.options
        if options.cwd is not None:
            raise UnsupportedCommandOptionError(
                "the VM executor does not support local cwd"
            )
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
