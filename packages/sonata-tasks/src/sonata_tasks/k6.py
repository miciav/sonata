from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import override

from sonata_engine import Task, TaskInputs, TaskOutcome

from sonata_tasks.core.fingerprint import fingerprint_digest
from sonata_tasks.execution.models import CommandOptions, CommandTaskSpec
from sonata_tasks.execution.ports import CommandTaskExecutor
from sonata_tasks.k6_models import K6Config, K6RunResult

K6ConfigSource = K6Config | Callable[[TaskInputs], K6Config]


def k6_argv(config: K6Config) -> tuple[str, ...]:
    argv = [
        "k6",
        "run",
        "--summary-export",
        str(config.summary_output_path),
        "--summary-trend-stats",
        "avg,min,med,max,p(50),p(90),p(95),p(99)",
    ]
    if config.vus is not None:
        argv.extend(("--vus", str(config.vus)))
    if config.duration is not None:
        argv.extend(("--duration", config.duration))
    if config.vus is None and config.duration is None:
        for stage in config.stages:
            argv.extend(("--stage", f"{stage.duration}:{stage.target}"))
    for key, value in config.env.items():
        argv.extend(("-e", f"{key}={value}"))
    argv.append(str(config.script_path))
    return tuple(argv)


class K6Task(Task[K6RunResult]):
    def __init__(
        self,
        config: K6ConfigSource,
        *,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str = "Run k6",
        semantic_key: str | None = None,
        require_pass: bool = False,
    ) -> None:
        if callable(config) and not semantic_key:
            raise ValueError("semantic_key is required for a dynamic k6 config")
        current = options or CommandOptions()
        if current.expected_exit_codes != frozenset({0}):
            raise ValueError("K6Task owns expected exit codes {0, 99}")
        self.title = title
        self._config = config
        self._executor = executor
        self._role = role
        self._options = replace(current, expected_exit_codes=frozenset({0, 99}))
        self._require_pass = require_pass
        self._semantic_key = semantic_key
        self._binding_key = executor.binding_key(role)

    @override
    def run(self, inputs: TaskInputs) -> TaskOutcome[K6RunResult]:
        config = self._config(inputs) if callable(self._config) else self._config
        started_at = datetime.now(timezone.utc)
        result = self._executor.run(
            CommandTaskSpec(
                task_id="",
                summary=self.title,
                argv=k6_argv(config),
                role=self._role,
                options=self._options,
            )
        )
        ended_at = datetime.now(timezone.utc)
        if result.return_code not in (0, 99):
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise RuntimeError(f"{self.title} failed (exit {result.return_code}): {detail}")
        if self._require_pass and result.return_code == 99:
            raise RuntimeError(f"{self.title} failed: k6 thresholds failed")
        return TaskOutcome(
            value=K6RunResult(
                config.summary_output_path, started_at, ended_at, result.return_code == 0
            )
        )

    @override
    def _fingerprint_payload(self) -> object:
        config: object
        if callable(self._config):
            config = {"dynamic": self._semantic_key}
        else:
            config = {
                "script_path": self._config.script_path,
                "summary_output_path": self._config.summary_output_path,
                "stages": tuple((stage.duration, stage.target) for stage in self._config.stages),
                "env": self._config.env,
                "vus": self._config.vus,
                "duration": self._config.duration,
            }
        return {
            "schema": 1,
            "digest": fingerprint_digest(
                {
                    "config": config,
                    "role": self._role,
                    "options": {
                        "cwd": self._options.cwd,
                        "env": self._options.env,
                        "remote_dir": self._options.remote_dir,
                        "expected_exit_codes": self._options.expected_exit_codes,
                        "timeout_seconds": self._options.timeout_seconds,
                    },
                    "require_pass": self._require_pass,
                    "binding_key": self._binding_key,
                }
            ),
        }
