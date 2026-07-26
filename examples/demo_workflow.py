"""Small runnable demo of the Sonata workflow engine.

Run with:
    uv run python examples/demo_workflow.py
"""

from __future__ import annotations

from sonata_engine import Resource, Task, TaskInputs, TaskOutcome, Workflow


class PrepareSource(Task[str]):
    title = "Prepare source"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        print("  preparing source tree...")
        return TaskOutcome(value="/tmp/src")


class Build(Task[str]):
    title = "Build image"

    def run(self, inputs: TaskInputs) -> TaskOutcome[str]:
        print("  building image...")
        return TaskOutcome(value="registry.example/demo:v1")


def _start_builder_vm(inputs: TaskInputs) -> str:
    print("  [resource] builder VM up")
    return "builder-vm-1"


def _stop_builder_vm(inputs: TaskInputs, vm_id: str) -> None:
    print(f"  [resource] builder VM down ({vm_id})")


class PushImage(Task[None]):
    title = "Push image"

    def run(self, inputs: TaskInputs) -> TaskOutcome[None]:
        print("  pushing image (needs the builder VM)...")
        return TaskOutcome()


def main() -> None:
    builder_vm = Resource(
        title="Acquire builder VM",
        acquire=_start_builder_vm,
        release=_stop_builder_vm,
    )

    workflow = Workflow(workflow_id="demo-release")
    workflow.add(PrepareSource())
    workflow.add(Build())
    workflow.add(PushImage(), requires=(builder_vm,))

    print("Running workflow...")
    result = workflow.run()

    print("\nCompiled task IDs and outcomes:")
    for execution in result.tasks:
        print(f"  {execution.task_id:<25} {execution.status:<8} {execution.outcome}")


if __name__ == "__main__":
    main()
