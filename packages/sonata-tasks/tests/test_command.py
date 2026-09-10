from __future__ import annotations

from pathlib import Path

import pytest
from sonata_engine import Steps, TaskInputs, Workflow

from sonata_tasks.core.command import Argv, CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.testing import RecordingExecutor

# CommandOptions is frozen, and a default argument is evaluated once at definition
# time anyway, so a shared module-level instance is not new sharing.
_DEFAULT_OPTIONS = CommandOptions()


def test_command_returns_result_and_builds_the_spec() -> None:
    executor = RecordingExecutor()
    task = CommandTask(
        title="Build",
        argv=("make", "all"),
        executor=executor,
        role="builder",
        options=CommandOptions(cwd=Path("/repo"), env={"MODE": "ci"}),
    )
    outcome = task.run(TaskInputs.empty())
    assert outcome.value is not None and outcome.value.ok
    assert executor.seen[0].role == "builder"
    assert executor.seen[0].options == task.options


def test_failure_reports_both_streams_and_verify_runs_only_after_success() -> None:
    failed = TaskResult("", "failed", 2, stderr="stderr", stdout="stdout")
    executor = RecordingExecutor(results=[failed])
    calls: list[TaskResult] = []
    task = CommandTask(
        title="Fail",
        argv=("false",),
        executor=executor,
        verify=calls.append,
        semantic_key="verify:v1",
    )
    with pytest.raises(RuntimeError, match=r"(?s)stderr.*stdout"):
        task.run(TaskInputs.empty())
    assert calls == []


@pytest.mark.parametrize(
    ("status", "code"),
    [("passed", 1), ("failed", 0), ("skipped", 0), ("passed", None)],
)
def test_executor_status_and_code_must_be_coherent(
    status: str, code: int | None
) -> None:
    executor = RecordingExecutor(results=[TaskResult("", status, code)])  # type: ignore[arg-type]
    task = CommandTask(title="X", argv=("true",), executor=executor)
    with pytest.raises(RuntimeError, match="incoherent"):
        task.run(TaskInputs.empty())


def test_dynamic_argv_and_verification_require_semantic_keys() -> None:
    executor = RecordingExecutor()
    with pytest.raises(ValueError, match="semantic_key"):
        CommandTask(title="X", argv=lambda _: ("true",), executor=executor)
    with pytest.raises(ValueError, match="semantic_key"):
        CommandTask(title="X", argv=("true",), executor=executor, verify=lambda _: None)


@pytest.mark.parametrize("argv", [(), ("",), ("true", 1)])
def test_dynamic_argv_is_validated_after_resolution(argv: tuple[object, ...]) -> None:
    task = CommandTask(
        title="X",
        argv=lambda _inputs: argv,  # type: ignore[return-value]
        executor=RecordingExecutor(),
        semantic_key="dynamic:v1",
    )

    with pytest.raises(ValueError, match="argv"):
        task.run(TaskInputs.empty())


def _fingerprint(task: CommandTask) -> str:
    workflow = Workflow(workflow_id="fingerprint")
    workflow.add(task)
    return workflow.compile().fingerprint


def test_every_semantic_command_input_changes_the_fingerprint() -> None:
    def make(
        *,
        argv: Argv = ("echo", "a"),
        role: str = "host",
        options: CommandOptions = _DEFAULT_OPTIONS,
        target: str = "local",
        semantic_key: str | None = None,
    ) -> CommandTask:
        return CommandTask(
            title="Same",
            argv=argv,
            executor=RecordingExecutor(target_key=target),
            role=role,
            options=options,
            semantic_key=semantic_key,
        )

    base = _fingerprint(make())
    variants = (
        make(argv=("echo", "b")),
        make(role="builder"),
        make(options=CommandOptions(cwd=Path("/repo"))),
        make(options=CommandOptions(env={"A": "B"})),
        make(options=CommandOptions(remote_dir="/srv")),
        make(options=CommandOptions(expected_exit_codes=frozenset({0, 17}))),
        make(options=CommandOptions(timeout_seconds=2)),
        make(target="different"),
        make(semantic_key="operation:v2"),
    )
    assert all(_fingerprint(task) != base for task in variants)


def test_mapping_order_does_not_affect_fingerprint_and_payload_hides_values() -> None:
    first = CommandTask(
        title="Same",
        argv=("echo", "secret-argv"),
        executor=RecordingExecutor(),
        options=CommandOptions(env={"TOKEN": "secret-token", "A": "B"}),
    )
    second = CommandTask(
        title="Same",
        argv=("echo", "secret-argv"),
        executor=RecordingExecutor(),
        options=CommandOptions(env={"A": "B", "TOKEN": "secret-token"}),
    )
    assert _fingerprint(first) == _fingerprint(second)
    assert "secret" not in repr(first._fingerprint_payload())


def test_dynamic_argv_is_not_called_during_compile_and_key_reaches_nested_steps() -> (
    None
):
    calls: list[TaskInputs] = []

    def argv(inputs: TaskInputs) -> tuple[str, ...]:
        calls.append(inputs)
        return ("true",)

    def nested(key: str) -> str:
        task = CommandTask(
            title="Dynamic", argv=argv, executor=RecordingExecutor(), semantic_key=key
        )
        workflow = Workflow(workflow_id="nested")
        workflow.add(Steps(title="Group", steps=(task,)))
        return workflow.compile().fingerprint

    first = nested("resolver:v1:a")
    second = nested("resolver:v1:b")
    assert calls == []
    assert first != second
