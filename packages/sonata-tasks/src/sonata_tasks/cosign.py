from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from sonata_tasks.command import CommandTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

COSIGN_IMAGE = (
    "gcr.io/projectsigstore/cosign@sha256:"
    "f1946d0f30fc8e3777b02f2201e02efdba9fe38f4918162f937052fac98e083f"
)
CosignOperation = Literal[
    "sign", "attest", "attach sbom", "verify", "verify-attestation", "public-key"
]
_ATTACH_SBOM = "attach sbom"
_KEY_COSIGN = "/key.cosign"


class CosignTask(CommandTask):
    def __init__(
        self,  # NOSONAR (S107): keyword-only tool configuration
        *,
        operation: CosignOperation | str,
        image: str,
        key_file: str,
        password_file: str,
        docker_config: str,
        executor: CommandTaskExecutor,
        role: str = "host",
        tool_image: str = COSIGN_IMAGE,
        predicate_type: str = "custom",
        sbom_type: str = "spdx",
        options: CommandOptions | None = None,
        title: str | None = None,
        verify: Callable[[TaskResult], None] | None = None,
        semantic_key: str | None = None,
        predicate_file: str | None = None,
        sbom_file: str | None = None,
        public_key_file: str | None = None,
        output_file: str | None = None,
    ) -> None:
        run = _docker_run_args(
            operation, docker_config, key_file, public_key_file, predicate_file, sbom_file
        )
        run.append(tool_image)
        run.extend(
            _cosign_command(
                operation, image, output_file, predicate_type=predicate_type, sbom_type=sbom_type
            )
        )
        if operation == "public-key":
            assert output_file is not None
            script = (
                'pw=$(cat "$1"); out="$2"; shift 2; COSIGN_PASSWORD="$pw" "$@" > "$out"'  # NOSONAR
            )
            positional = (password_file, output_file, *run)
        else:
            script = 'pw=$(cat "$1"); shift; COSIGN_PASSWORD="$pw" exec "$@"'  # NOSONAR
            positional = (password_file, *run)
        super().__init__(
            title=title or f"cosign {operation} {image or key_file}",
            argv=("sh", "-c", script, "--", *positional),
            executor=executor,
            role=role,
            options=options,
            verify=verify,
            semantic_key=semantic_key,
        )


def _docker_run_args(
    operation: CosignOperation | str,
    docker_config: str,
    key_file: str,
    public_key_file: str | None,
    predicate_file: str | None,
    sbom_file: str | None,
) -> list[str]:
    run = [
        "docker",
        "run",
        "--rm",
        "--user",
        "0",
        "-e",
        "COSIGN_PASSWORD",
        "-e",
        "DOCKER_CONFIG=/auth",
        "-v",
        f"{docker_config}:/auth:ro",
    ]
    if operation in ("sign", "attest", "public-key"):
        run.extend(("-v", f"{key_file}:/key.cosign:ro"))
    if public_key_file is not None and operation in ("verify", "verify-attestation"):
        run.extend(("-v", f"{public_key_file}:/pub.key:ro"))
    if predicate_file is not None and operation == "attest":
        run.extend(("-v", f"{predicate_file}:/predicate.json:ro"))
    if sbom_file is not None and operation == _ATTACH_SBOM:
        run.extend(("-v", f"{sbom_file}:/sbom.json:ro"))
    return run


def _cosign_command(
    operation: CosignOperation | str,
    image: str,
    output_file: str | None,
    *,
    predicate_type: str = "custom",
    sbom_type: str = "spdx",
) -> list[str]:
    if operation == "sign":
        return ["sign", "--yes", "--key", _KEY_COSIGN, image]
    if operation == "attest":
        return [
            "attest",
            "--yes",
            "--key",
            _KEY_COSIGN,
            "--type",
            predicate_type,
            "--predicate",
            "/predicate.json",
            image,
        ]
    if operation == _ATTACH_SBOM:
        return ["attach", "sbom", "--sbom", "/sbom.json", "--type", sbom_type, image]
    if operation == "verify":
        return ["verify", "--key", "/pub.key", image]
    if operation == "verify-attestation":
        return ["verify-attestation", "--key", "/pub.key", "--type", predicate_type, image]
    if operation == "public-key":
        if output_file is None:
            raise ValueError("cosign public-key needs an output_file")
        return ["public-key", "--key", _KEY_COSIGN]
    raise ValueError(f"unknown cosign operation: {operation}")
