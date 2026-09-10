from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sonata_engine import Resource, TaskInputs
from sonata_tasks.command import CommandTask
from sonata_tasks.compensation import compensated_resource
from sonata_tasks.execution.models import CommandOptions
from sonata_tasks.execution.ports import CommandTaskExecutor


@dataclass(frozen=True, slots=True)
class HelmReleaseSpec:
    release: str
    chart: str
    namespace: str
    values: tuple[str, ...]
    timeout: str = "5m"


class HelmInstallTask(CommandTask):
    def __init__(
        self,
        spec: HelmReleaseSpec,
        *,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
    ) -> None:
        super().__init__(
            title=f"Install Helm release {spec.release}",
            argv=(
                "helm",
                "upgrade",
                "--install",
                spec.release,
                spec.chart,
                "--namespace",
                spec.namespace,
                "--create-namespace",
                "--wait",
                "--timeout",
                spec.timeout,
                *spec.values,
            ),
            executor=executor,
            role=role,
            options=options,
        )


class HelmUninstallTask(CommandTask):
    def __init__(
        self,
        spec: HelmReleaseSpec,
        *,
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
    ) -> None:
        super().__init__(
            title=f"Uninstall Helm release {spec.release}",
            argv=(
                "helm",
                "uninstall",
                spec.release,
                "--namespace",
                spec.namespace,
                "--ignore-not-found",
                "--wait",
            ),
            executor=executor,
            role=role,
            options=options,
        )


def helm_release_resource(
    spec: HelmReleaseSpec,
    *,
    executor: CommandTaskExecutor,
    role: str = "host",
    options: CommandOptions | None = None,
    requires: tuple[Resource[Any], ...] = (),
) -> Resource[HelmReleaseSpec]:
    install = HelmInstallTask(spec, executor=executor, role=role, options=options)
    uninstall = HelmUninstallTask(spec, executor=executor, role=role, options=options)

    def acquire(inputs: TaskInputs) -> HelmReleaseSpec:
        _ = install.run(inputs)
        return spec

    return compensated_resource(
        title=f"Acquire Helm release {spec.release}",
        acquire=acquire,
        compensate=uninstall.run,
        requires=requires,
    )
