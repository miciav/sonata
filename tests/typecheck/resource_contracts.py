"""Positive static-check fixture for typed resource inputs."""

from typing import assert_type

from sonata_engine import Resource, TaskInputs

resource: Resource[str] = Resource(
    title="Acquire name",
    acquire=lambda _inputs: "builder",
    release=lambda _inputs, _value: None,
)


def read_name(inputs: TaskInputs) -> None:
    assert_type(inputs.resource(resource), str)
