from __future__ import annotations

import pytest

from sonata_tasks.execution.bindings import RoleBindings, RoleBoundCommandTaskExecutor
from sonata_tasks.execution.models import CommandTaskSpec
from sonata_tasks.testing import RecordingExecutor


def test_client_defined_role_dispatches_and_exposes_target_identity() -> None:
    builder = RecordingExecutor(target_key="ssh:builder@example")
    router = RoleBoundCommandTaskExecutor(RoleBindings({"builder": builder}))
    task = CommandTaskSpec("build", "Build", ("make",), role="builder")

    assert router.run(task).ok
    assert builder.seen == [task]
    assert router.binding_key("builder") == "ssh:builder@example"


def test_missing_binding_fails_before_any_backend_runs() -> None:
    host = RecordingExecutor()
    router = RoleBoundCommandTaskExecutor(RoleBindings({"host": host}))
    with pytest.raises(ValueError, match="no executor bound for role 'builder'"):
        router.run(CommandTaskSpec("x", "X", ("true",), role="builder"))
    assert host.seen == []


@pytest.mark.parametrize("bindings", [{}, {"": RecordingExecutor()}])
def test_bindings_require_non_empty_roles(
    bindings: dict[str, RecordingExecutor],
) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        RoleBindings(bindings)
