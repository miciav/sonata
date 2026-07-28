"""Small runnable demo of the Sonata workflow engine.

Run with:
    uv run python examples/demo_workflow.py
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sonata_engine import (
    Resource,
    Steps,
    Task,
    TaskInputs,
    TaskOutcome,
    Workflow,
    WorkflowEvent,
    bind_workflow_sink,
    workflow_log,
)

IMAGES = ("control-plane", "function-runtime")


class ConsoleSink:
    """The whole sink contract: `emit`, plus a `status` context manager.

    Without one bound, the engine reports nowhere and `subtask` is a no-op.
    """

    _MARKS = {"task.started": "->", "task.passed": "ok", "task.failed": "XX"}

    def emit(self, event: WorkflowEvent) -> None:
        if event.kind == "log.line":
            print(f"        . {event.line}")
            return
        indent = "    " if event.parent_task_id else ""
        print(f"  {indent}{self._MARKS.get(event.kind, event.kind)} {event.task_id}")

    @contextmanager
    def status(self, label: str) -> Generator[None, None, None]:
        yield


class PrepareSource(Task[str]):
    title = "Prepare source"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        workflow_log("preparing source tree...")
        return TaskOutcome(value="/tmp/src")


class BuildImage(Task[tuple[str, ...]]):
    """One image build. The same class occupies every position in the chain:
    the first instance starts the tuple; later instances extend it.
    """

    def __init__(self, image: str, *, first: bool = False) -> None:
        self.title = f"Build {image}"
        self._image = image
        self._first = first

    def run(self, inputs: TaskInputs) -> TaskOutcome[tuple[str, ...]]:
        workflow_log(f"docker build {self._image}")
        built = () if self._first else inputs.upstream()
        return TaskOutcome(value=(*built, f"registry.example/{self._image}:v1"))


class ScanImages(Task[tuple[str, ...]]):
    title = "Scan for vulnerabilities"

    def run(self, inputs: TaskInputs) -> TaskOutcome[tuple[str, ...]]:
        built = inputs.upstream()
        workflow_log(f"scanning {len(built)} images")
        return TaskOutcome(value=built)


def _start_builder_vm(inputs: TaskInputs) -> str:
    workflow_log("[resource] builder VM up")
    return "builder-vm-1"


def _stop_builder_vm(inputs: TaskInputs, vm_id: str) -> None:
    workflow_log(f"[resource] builder VM down ({vm_id})")


class PushImages(Task[None]):
    title = "Push images"

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        workflow_log("pushing images (needs the builder VM)...")
        return TaskOutcome()


def main() -> None:
    builder_vm = Resource(
        title="Acquire builder VM",
        acquire=_start_builder_vm,
        release=_stop_builder_vm,
    )

    workflow = Workflow(workflow_id="demo-release")
    workflow.add(PrepareSource())
    workflow.add(
        Steps(
            title="Build images",
            steps=(
                *(
                    BuildImage(image, first=index == 0)
                    for index, image in enumerate(IMAGES)
                ),
                ScanImages(),
            ),
        )
    )
    workflow.add(PushImages(), requires=(builder_vm,))

    print("Running workflow (indented lines are steps):")
    with bind_workflow_sink(ConsoleSink()):
        result = workflow.run()

    print("\nCompiled task IDs and outcomes:")
    for execution in result.tasks:
        # `outcome` is None exactly when the unit was skipped, so it is not
        # interchangeable with an outcome whose value happens to be None.
        outcome = execution.outcome if execution.outcome else "(skipped)"
        print(f"  {execution.task_id:<25} {execution.status:<8} {outcome}")

    print(
        f"\n{len(IMAGES)} images built as {len(IMAGES) + 1} reported steps, "
        "inside 1 compiled unit."
    )


if __name__ == "__main__":
    main()
