from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sonata_tasks.core.fingerprint import fingerprint_digest, semantic_key


def test_canonical_fingerprint_is_stable_for_reordered_nested_values() -> None:
    first = {
        "mapping": {"b": 2, "a": 1},
        "set": {"beta", "alpha"},
        "sequence": [Path("a/b"), bytearray(b"payload")],
    }
    second = {
        "sequence": [Path("a/b"), b"payload"],
        "set": {"alpha", "beta"},
        "mapping": {"a": 1, "b": 2},
    }

    assert fingerprint_digest(first) == fingerprint_digest(second)


def test_semantic_key_is_stable_in_a_distinct_process() -> None:
    expected = semantic_key("http-verify:v1", {"labels": {"b": "2", "a": "1"}})
    script = (
        "from sonata_tasks.core.fingerprint import semantic_key; "
        "print(semantic_key('http-verify:v1', {'labels': {'a': '1', 'b': '2'}}))"
    )
    environment = os.environ.copy()
    source_root = str(Path(__file__).parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (source_root, environment.get("PYTHONPATH")))
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.stdout.strip() == expected


def test_semantic_key_is_opaque() -> None:
    key = semantic_key("request:v1", {"authorization": "Bearer super-secret"})

    assert key.startswith("request:v1:sha256:")
    assert "super-secret" not in key


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"value": float("inf")}, ValueError),
        ({1: "not-a-string-key"}, TypeError),
        ({"value": object()}, TypeError),
    ],
)
def test_fingerprint_rejects_noncanonical_values(
    payload: dict, error: type[Exception]
) -> None:
    with pytest.raises(error):
        fingerprint_digest(payload)


def test_semantic_key_requires_a_namespace() -> None:
    with pytest.raises(ValueError, match="namespace"):
        semantic_key("", {"value": 1})
