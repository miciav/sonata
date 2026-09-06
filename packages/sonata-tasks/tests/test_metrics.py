from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sonata_tasks.metrics import PrometheusMinimumCheckTask, PrometheusScrapeCheckTask
from sonata_tasks.tasks.models import CommandTaskSpec, TaskResult

from sonata_engine import TaskInputs


@dataclass
class Executor:
    stdout: str
    seen: list[CommandTaskSpec] = field(default_factory=list)

    def binding_key(self, role: str) -> str:
        return f"test:{role}"

    def run(self, task: CommandTaskSpec, *, dry_run: bool = False) -> TaskResult:
        self.seen.append(task)
        return TaskResult(task_id="", status="passed", return_code=0, stdout=self.stdout)


def test_metric_check_sums_matching_samples_and_honors_labels() -> None:
    executor = Executor(
        'function_success_total{function="word-stats"} 1\n'
        'function_success_total{function="word-stats"} 2\n'
        'function_success_total{function="other"} 100\n'
    )

    _ = PrometheusMinimumCheckTask(
        url="http://cp:8081/actuator/prometheus",
        minimums=(("function_success_total", {"function": "word-stats"}, 3),),
        executor=executor,
        role="stack",
    ).run(TaskInputs.empty())


def test_metric_check_rejects_a_metric_below_the_requested_minimum() -> None:
    executor = Executor('function_cold_start_total{function="word-stats"} 0\n')
    task = PrometheusMinimumCheckTask(
        url="http://cp:8081/actuator/prometheus",
        minimums=(("function_cold_start_total", {"function": "word-stats"}, 1),),
        executor=executor,
        role="stack",
    )

    with pytest.raises(RuntimeError, match="function_cold_start_total"):
        _ = task.run(TaskInputs.empty())


def test_metric_check_accepts_the_first_available_alternative() -> None:
    executor = Executor('function_init_duration_ms_count{function="word-stats"} 1\n')

    _ = PrometheusMinimumCheckTask(
        url="http://cp:8081/actuator/prometheus",
        minimums=(),
        any_minimums=(
            (
                ("function_init_duration_ms_seconds_count", "function_init_duration_ms_count"),
                {"function": "word-stats"},
                1,
            ),
        ),
        executor=executor,
        role="stack",
    ).run(TaskInputs.empty())


def test_scrape_check_requires_and_rejects_samples() -> None:
    executor = Executor("job_requests_total 3\n")
    PrometheusScrapeCheckTask(
        url="https://metrics.example/custom",
        expect=("job_requests_total",),
        reject=("nanofaas_internal",),
        executor=executor,
    ).run(TaskInputs.empty())

    assert executor.seen[0].argv[-1] == "https://metrics.example/custom"


def test_scrape_check_reports_missing_and_forbidden_samples() -> None:
    missing = PrometheusScrapeCheckTask(
        url="https://metrics.example/custom",
        expect=("wanted",),
        executor=Executor("other 1\n"),
    )
    forbidden = PrometheusScrapeCheckTask(
        url="https://metrics.example/custom",
        reject=("forbidden",),
        executor=Executor("forbidden 1\n"),
    )

    with pytest.raises(RuntimeError, match="expected sample"):
        missing.run(TaskInputs.empty())
    with pytest.raises(RuntimeError, match="should not"):
        forbidden.run(TaskInputs.empty())


def test_metric_fingerprint_is_stable_for_reordered_labels() -> None:
    first = PrometheusMinimumCheckTask(
        url="https://metrics.example/custom",
        minimums=(("requests", {"tenant": "one", "method": "GET"}, 1),),
        executor=Executor(""),
    )
    second = PrometheusMinimumCheckTask(
        url="https://metrics.example/custom",
        minimums=(("requests", {"method": "GET", "tenant": "one"}, 1),),
        executor=Executor(""),
    )

    assert first._fingerprint_payload() == second._fingerprint_payload()


def test_dynamic_metrics_endpoint_is_resolved_only_when_run() -> None:
    calls: list[object] = []

    def endpoint(inputs: TaskInputs) -> str:
        calls.append(inputs)
        return "https://metrics.example/dynamic"

    executor = Executor("wanted 1\n")
    task = PrometheusScrapeCheckTask(
        url=endpoint,
        expect=("wanted",),
        executor=executor,
        semantic_key="logical-metrics-resource",
    )
    assert calls == []

    task.run(TaskInputs.empty())

    assert len(calls) == 1
    assert executor.seen[0].argv[-1] == "https://metrics.example/dynamic"


def test_metric_check_reports_absent_alternatives() -> None:
    task = PrometheusMinimumCheckTask(
        url="https://metrics.example/custom",
        minimums=(),
        any_minimums=((("first", "second"), {}, 1),),
        executor=Executor("other 1\n"),
    )

    with pytest.raises(RuntimeError, match="none of"):
        task.run(TaskInputs.empty())
