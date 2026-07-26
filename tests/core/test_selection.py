from __future__ import annotations

import dataclasses

import pytest

from sonata_engine.core.selection import Selection
from sonata_engine.errors import SelectionError


def test_empty_selection_is_no_filter() -> None:
    assert Selection().is_empty is True


def test_any_field_makes_the_selection_a_filter() -> None:
    assert Selection(only="build").is_empty is False
    assert Selection(start="build").is_empty is False
    assert Selection(until="build").is_empty is False


def test_only_is_mutually_exclusive_with_start() -> None:
    with pytest.raises(SelectionError, match="mutually exclusive"):
        Selection(only="build", start="publish")


def test_only_is_mutually_exclusive_with_until() -> None:
    with pytest.raises(SelectionError, match="mutually exclusive"):
        Selection(only="build", until="publish")


def test_start_and_until_may_be_combined() -> None:
    selection = Selection(start="build", until="publish")

    assert (selection.start, selection.until) == ("build", "publish")


def test_selection_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        Selection().only = "build"  # type: ignore[misc]
