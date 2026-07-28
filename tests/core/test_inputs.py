from dataclasses import replace

import pytest

from sonata_engine import NoUpstreamValueError, TaskInputs


def test_upstream_raises_when_no_step_produced_a_value() -> None:
    with pytest.raises(NoUpstreamValueError, match="no upstream value"):
        TaskInputs.empty().upstream()


def test_upstream_returns_the_value_it_was_built_with() -> None:
    inputs = replace(TaskInputs.empty(), _upstream="rel-42")
    assert inputs.upstream() == "rel-42"


def test_upstream_returns_none_as_a_legitimate_value() -> None:
    """None is a value a step may return; it must not read as 'no upstream'."""
    inputs = replace(TaskInputs.empty(), _upstream=None)
    assert inputs.upstream() is None


def test_existing_construction_paths_still_work() -> None:
    """`empty()` and the two-argument constructor predate the new field."""
    for inputs in (TaskInputs.empty(), TaskInputs({}, frozenset())):
        with pytest.raises(NoUpstreamValueError):
            inputs.upstream()
