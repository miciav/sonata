from __future__ import annotations

import math
from pathlib import Path
from types import MappingProxyType

import pytest

from sonata_tasks.execution.models import CommandOptions, CommandTaskSpec, TaskResult


def test_options_are_defensively_immutable() -> None:
    env = {"A": "B"}
    options = CommandOptions(env=env, expected_exit_codes=frozenset({0, 17}))
    env["A"] = "changed"
    assert isinstance(options.env, MappingProxyType)
    assert dict(options.env) == {"A": "B"}
    assert options.expected_exit_codes == frozenset({0, 17})


@pytest.mark.parametrize("timeout", [0, -1, math.inf, math.nan])
def test_timeout_must_be_finite_and_positive(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        CommandOptions(timeout_seconds=timeout)


def test_options_reject_empty_exit_codes_and_non_string_environment() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        CommandOptions(expected_exit_codes=frozenset())
    with pytest.raises(TypeError, match="strings"):
        CommandOptions(env={"A": 1})  # type: ignore[dict-item]


def test_spec_has_one_options_source_and_a_single_role_default() -> None:
    options = CommandOptions(cwd=Path("/repo"), remote_dir="/srv/app")
    spec = CommandTaskSpec(
        task_id="x", summary="X", argv=("echo", "ok"), options=options
    )
    assert spec.role == "host"
    assert spec.execution_role == "host"
    assert spec.options is options
    assert not hasattr(spec, "cwd")


def test_spec_rejects_empty_role_and_argv() -> None:
    with pytest.raises(ValueError, match="argv"):
        CommandTaskSpec(task_id="x", summary="X", argv=())
    with pytest.raises(ValueError, match="role"):
        CommandTaskSpec(task_id="x", summary="X", argv=("true",), role="")


def test_result_requires_a_real_success_code_to_be_ok() -> None:
    assert TaskResult("x", "passed", 17, frozenset({17})).ok
    assert not TaskResult("x", "passed", None).ok
    assert not TaskResult("x", "failed", 0).ok
