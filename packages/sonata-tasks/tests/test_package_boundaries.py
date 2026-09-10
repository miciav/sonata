from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "sonata_tasks.vm",
        "sonata_tasks.prometheus",
        "sonata_tasks.vm.multipass",
        "sonata_tasks.vm.azure",
        "sonata_tasks.shell",
        "sonata_tasks.ansible",
        "sonata_tasks.vm.adapters",
        "sonata_tasks.http",
        "sonata_tasks.k6",
        "sonata_tasks.metrics",
        "sonata_tasks.helm",
        "sonata_tasks.compose",
        "sonata_tasks.composites",
    ],
)
def test_catalog_module_does_not_import_nanolab(module: str) -> None:
    code = (
        "import importlib,sys; "
        f"importlib.import_module({module!r}); "
        "assert not any(key.startswith('nanolab') for key in sys.modules)"
    )
    result = subprocess.run(
        (sys.executable, "-c", code),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
