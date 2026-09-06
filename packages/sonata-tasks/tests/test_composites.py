from pathlib import Path

from sonata_tasks.composites import command_specs_composite, command_specs_fingerprint
from sonata_tasks.execution.models import CommandOptions, CommandTaskSpec
from sonata_tasks.testing import RecordingExecutor

from sonata_engine import Workflow


def _spec(*argv: str, role: str = "builder", summary: str = "Build artifact") -> CommandTaskSpec:
    return CommandTaskSpec(
        task_id="ignored",
        summary=summary,
        argv=argv,
        role=role,
        options=CommandOptions(cwd=Path("/workspace"), env={"MODE": "release"}),
    )


def test_command_specs_composite_runs_the_original_specs_in_order() -> None:
    executor = RecordingExecutor(target_key="builder-one")
    steps = command_specs_composite(
        (_spec("tool", "build"), _spec("tool", "inspect", summary="Inspect artifact")),
        executor,
        title="Build and inspect",
    )

    workflow = Workflow("command-specs")
    workflow.add(steps)
    workflow.run()

    assert [item.argv for item in executor.seen] == [
        ("tool", "build"),
        ("tool", "inspect"),
    ]
    assert executor.seen[0].options.env == {"MODE": "release"}


def test_command_specs_fingerprint_covers_order_options_role_and_binding() -> None:
    one = _spec("tool", "build")
    reordered_env = CommandTaskSpec(
        task_id="other",
        summary="Other title",
        argv=one.argv,
        role=one.role,
        options=CommandOptions(cwd=Path("/workspace"), env={"MODE": "release"}),
    )
    first = RecordingExecutor(target_key="builder-one")

    assert command_specs_fingerprint((one,), first) == command_specs_fingerprint(
        (reordered_env,), first
    )
    assert command_specs_fingerprint((one,), first) != command_specs_fingerprint(
        (one,), RecordingExecutor(target_key="builder-two")
    )
    assert command_specs_fingerprint((one,), first) != command_specs_fingerprint(
        (_spec("tool", "inspect"),), first
    )
