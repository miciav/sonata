"""Keep Sonata free of downstream-product imports and runtime dependencies.

Sonata is a product-independent workflow engine that downstream products
(nanoFaaS, controlplane-tool, VM-provider SDKs) will depend on -- never the
other way around.
"""

import ast
import tomllib
from pathlib import Path

import sonata_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "sonata_engine"

FORBIDDEN = {
    "controlplane_tool",
    "nanofaas",
    "nanolab",
    "sonata_tasks",
    "workflow_tasks",
    "azure_vm_sdk",
    "multipass_sdk",
    "proxmox_sdk",
}


def _iter_imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.level == 0
        ):
            roots.add(node.module.split(".")[0])
    return roots


def test_no_forbidden_imports_under_src() -> None:
    violations: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        forbidden_hits = _iter_imported_roots(tree) & FORBIDDEN
        violations.extend(
            f"{path.relative_to(REPO_ROOT)} imports forbidden package '{root}'"
            for root in forbidden_hits
        )

    assert not violations, (
        "Sonata must stay independent of downstream products:\n" + "\n".join(violations)
    )


def test_no_runtime_dependencies() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)

    assert pyproject["project"]["dependencies"] == []


def test_root_exports_the_v2_contract() -> None:
    expected = {
        "CorruptJournalError",
        "Resource",
        "ResourceDependencyCycleError",
        "TaskExecution",
        "TaskInputs",
        "TaskOutcome",
        "Workflow",
        "WorkflowResult",
        "WorkflowTopologyMismatchError",
    }

    assert expected <= set(sonata_engine.__all__)


def test_engine_owned_resource_operations_are_not_public() -> None:
    assert "ResourceOp" not in sonata_engine.__all__
    assert "ResourceOperation" not in sonata_engine.__all__
