"""The local executor: runs argv in a child process and captures its output."""

from __future__ import annotations

import os
import signal
import subprocess

from sonata_tasks.errors import CommandTimeoutError, UnsupportedCommandOptionError
from sonata_tasks.execution.models import CommandTaskSpec, TaskResult


class LocalCommandTaskExecutor:
    """Execute argv locally without a shell and collect its output."""

    def __init__(self, *, target_key: str = "local") -> None:
        """Store the binding key reported for every role.

        Raises:
            ValueError: If ``target_key`` is empty.

        """
        if not target_key:
            raise ValueError("target_key must not be empty")
        self._target_key = target_key

    def binding_key(self, role: str) -> str:
        """Return ``target_key``; the role is ignored on a single host."""
        del role
        return self._target_key

    @staticmethod
    def _stop(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        """Run ``task`` in a child process and classify its exit code.

        A ``dry_run`` returns a passing result without spawning anything. On
        timeout the whole process group is stopped and the output collected so
        far is attached to the raised error.

        Raises:
            UnsupportedCommandOptionError: If ``remote_dir`` is set, or
                ``timeout_seconds`` is set on a non-POSIX platform.
            CommandTimeoutError: If the process outlives ``timeout_seconds``.

        """
        options = task.options
        if options.remote_dir is not None:
            raise UnsupportedCommandOptionError(
                "the local executor does not support remote_dir"
            )
        if options.timeout_seconds is not None and os.name != "posix":
            raise UnsupportedCommandOptionError(
                "timeout_seconds is currently supported only on POSIX"
            )
        if dry_run:
            return TaskResult(
                task_id=task.task_id,
                status="passed",
                return_code=0,
                expected_exit_codes=options.expected_exit_codes,
            )

        env = os.environ.copy()
        env.update(options.env)
        process = subprocess.Popen(
            task.argv,
            cwd=options.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
        )
        try:
            stdout, stderr = process.communicate(timeout=options.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._stop(process)
            stdout, stderr = process.communicate()
            raise CommandTimeoutError(
                task.argv,
                options.timeout_seconds or 0,
                stdout=stdout,
                stderr=stderr,
            ) from None
        except BaseException:
            self._stop(process)
            _ = process.communicate()
            raise

        return _result(task, process.returncode, stdout, stderr)


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
