"""Releasing what a kept run left behind, from a later process.

`keep` holds resources past the end of a run so the next one need not rebuild
them. Whoever asked for that eventually has to give them back, and by then the
`_RunState` that held their acquired values is gone. The journal's retention
records are what survives, so releasing from a later process means reading them
rather than re-running the workflow that created them.

Deliberately not fingerprint-checked. Resume refuses a journal whose workflow
shape changed, because reusing evidence across a different topology is unsound.
Releasing is the opposite: a VM left running does not care that the DAG moved on,
and refusing to release it is how it stays up.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sonata_engine.core.inputs import TaskInputs
from sonata_engine.core.resource_task import Resource
from sonata_engine.journal import SCHEMA_VERSION, JournalConfig


class UnknownRetainedResourceError(LookupError):
    """The journal names a retained resource the caller did not supply.

    Skipping it would report a clean teardown while the resource keeps running,
    which is the failure this whole path exists to remove.
    """


def _records(config: JournalConfig) -> list[dict[str, Any]]:
    if not config.path.exists():
        return []
    return [
        record
        for record in (
            json.loads(line)
            for line in config.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if isinstance(record, dict)
    ]


def _outstanding(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retention records with no matching release, newest state per resource."""
    released = {
        record.get("resource") for record in records if record.get("kind") == "released"
    }
    return [
        record
        for record in records
        if record.get("kind") == "retained" and record.get("resource") not in released
    ]


def release_retained(
    resources: Mapping[str, Resource[Any]], journal: JournalConfig
) -> tuple[str, ...]:
    """Release every resource the journal still records as retained.

    Released in the order the records were written, which is already the order a
    run itself would have used: release units are spliced in reverse of
    acquisition, so the retention counter recorded them reverse-acquired. Every
    resource is attempted even when an earlier release fails -- stopping at the
    first would leave the rest running, which is the harm being addressed --
    and the errors are raised together at the end.

    Returns the titles released, in the order they were released.
    """
    outstanding = _outstanding(_records(journal))
    if not outstanding:
        return ()

    missing = sorted(
        {
            str(record.get("resource"))
            for record in outstanding
            if str(record.get("resource")) not in resources
        }
    )
    if missing:
        raise UnknownRetainedResourceError(
            "journal records retained resources the caller did not supply: "
            + ", ".join(missing)
        )

    inputs = TaskInputs._for_resource_values({}, ())  # noqa: SLF001
    errors: list[BaseException] = []
    released: list[str] = []
    for record in sorted(outstanding, key=lambda item: int(item.get("order", 0))):
        title = str(record["resource"])
        resource = resources[title]
        value = record.get("value")
        try:
            if resource.revive is not None:
                value = resource.revive(value)
            resource.release(inputs, value)
        except BaseException as error:  # noqa: BLE001 - reported together below
            errors.append(error)
            continue
        _record_released(journal, record, title)
        released.append(title)

    if errors:
        if len(errors) == 1:
            raise errors[0]
        combined = RuntimeError(
            "releasing retained resources failed:\n"
            + "\n".join(str(error) for error in errors)
        )
        for error in errors:
            combined.add_note(str(error))
        raise combined
    return tuple(released)


def _record_released(journal: JournalConfig, record: dict[str, Any], title: str) -> None:
    entry = {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": record.get("workflow_id"),
        "workflow_fingerprint": record.get("workflow_fingerprint"),
        "run_id": record.get("run_id"),
        "kind": "released",
        "resource": title,
    }
    with open(journal.path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
        handle.flush()
