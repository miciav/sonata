"""Releasing what a kept run left behind, from a later process."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from sonata_engine import Resource, Workflow
from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.outcome import TaskOutcome
from sonata_engine.core.task import Task
from sonata_engine.journal import JournalConfig
from sonata_engine.retention import UnknownRetainedResourceError, release_retained


@dataclasses.dataclass(frozen=True)
class _VmInfo:
    name: str
    host: str


class _Use(Task[None]):
    title = "Use"

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        del inputs
        return TaskOutcome()


def _resource(title: str, value: object, calls: list[str], **kwargs) -> Resource:
    return Resource(
        title=title,
        acquire=lambda _inputs: value,
        release=lambda _inputs, released: calls.append(f"{title}:{released}"),
        **kwargs,
    )


def _kept_run(tmp_path: Path, resources: tuple[Resource, ...], calls: list[str]) -> Path:
    workflow = Workflow(workflow_id="wf", keep=True)
    workflow.add(_Use(), requires=resources)
    path = tmp_path / "journal.jsonl"
    workflow.run(journal=JournalConfig(path))
    assert calls == []
    return path


def test_releases_retained_resources_in_reverse_order(tmp_path: Path) -> None:
    calls: list[str] = []
    stack = _resource("Acquire stack", "s", calls)
    loadgen = _resource("Acquire loadgen", "l", calls)
    path = _kept_run(tmp_path, (stack, loadgen), calls)

    release_retained({r.title: r for r in (stack, loadgen)}, JournalConfig(path))

    assert calls == ["Acquire loadgen:l", "Acquire stack:s"]


def test_revive_rebuilds_the_value_the_release_expects(tmp_path: Path) -> None:
    """The journal can only hold JSON, but a release written against a dataclass
    would break on a dict. The resource says how to read its own record back."""
    seen: list[object] = []
    vm = Resource(
        title="Acquire vm",
        acquire=lambda _inputs: _VmInfo(name="stack", host="10.0.0.1"),
        release=lambda _inputs, info: seen.append(info),
        revive=lambda raw: _VmInfo(**raw),
    )
    workflow = Workflow(workflow_id="wf", keep=True)
    workflow.add(_Use(), requires=(vm,))
    path = tmp_path / "journal.jsonl"
    workflow.run(journal=JournalConfig(path))

    release_retained({vm.title: vm}, JournalConfig(path))

    assert seen == [_VmInfo(name="stack", host="10.0.0.1")]


def test_a_second_teardown_is_a_no_op(tmp_path: Path) -> None:
    calls: list[str] = []
    stack = _resource("Acquire stack", "s", calls)
    path = _kept_run(tmp_path, (stack,), calls)
    config = JournalConfig(path)

    release_retained({stack.title: stack}, config)
    release_retained({stack.title: stack}, config)

    assert calls == ["Acquire stack:s"]


def test_a_retained_resource_the_caller_cannot_build_is_reported(tmp_path: Path) -> None:
    """Silently skipping it would report a clean teardown while the thing keeps
    running -- the failure mode this whole feature exists to remove."""
    calls: list[str] = []
    stack = _resource("Acquire stack", "s", calls)
    path = _kept_run(tmp_path, (stack,), calls)

    with pytest.raises(UnknownRetainedResourceError, match="Acquire stack"):
        release_retained({}, JournalConfig(path))

    assert calls == []


def test_one_failing_release_does_not_strand_the_others(tmp_path: Path) -> None:
    calls: list[str] = []
    stack = _resource("Acquire stack", "s", calls)
    broken = Resource(
        title="Acquire broken",
        acquire=lambda _inputs: "b",
        release=_boom,
    )
    path = _kept_run(tmp_path, (stack, broken), calls)

    with pytest.raises(RuntimeError, match="boom"):
        release_retained({r.title: r for r in (stack, broken)}, JournalConfig(path))

    assert calls == ["Acquire stack:s"]


def _boom(_inputs: TaskInputs, _value: object) -> None:
    raise RuntimeError("boom")


def test_records_the_release_so_the_journal_stays_truthful(tmp_path: Path) -> None:
    calls: list[str] = []
    stack = _resource("Acquire stack", "s", calls)
    path = _kept_run(tmp_path, (stack,), calls)

    release_retained({stack.title: stack}, JournalConfig(path))

    kinds = [
        json.loads(line).get("kind")
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    assert kinds.count("retained") == 1
    assert kinds.count("released") == 1
