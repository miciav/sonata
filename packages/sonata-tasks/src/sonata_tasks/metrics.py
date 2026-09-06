from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from functools import partial

from sonata_engine import TaskInputs
from sonata_tasks.command import CommandTask
from sonata_tasks.core.fingerprint import semantic_key as build_semantic_key
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.execution.ports import CommandTaskExecutor

MetricsEndpoint = str | Callable[[TaskInputs], str]
_METRIC_NAME = r"[A-Za-z_:][A-Za-z0-9_:]*"
_SAMPLE = re.compile(
    rf"^({_METRIC_NAME})(?:\{{([^}}]*)\}})?\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
)
_LABEL = re.compile(r'([A-Za-z_]\w*)="((?:[^"\\]|\\.)*)"')


def metric_sum(scrape: str, name: str, labels: Mapping[str, str]) -> tuple[int, float]:
    matches, total = 0, 0.0
    for line in scrape.splitlines():
        match = _SAMPLE.match(line)
        if match is None or match.group(1) != name:
            continue
        actual = {
            key: value.replace(r"\"", '"').replace(r"\\", "\\")
            for key, value in _LABEL.findall(match.group(2) or "")
        }
        if all(actual.get(key) == value for key, value in labels.items()):
            matches += 1
            total += float(match.group(3))
    return matches, total


def _resolve(endpoint: MetricsEndpoint, inputs: TaskInputs) -> str:
    return endpoint(inputs) if callable(endpoint) else endpoint


def _require_metric_sum(
    url: object, scrape: str, names: tuple[str, ...], labels: Mapping[str, str], minimum: float
) -> None:
    for name in names:
        matches, total = metric_sum(scrape, name, labels)
        if matches:
            if total < minimum:
                raise RuntimeError(
                    f"{url}: {name}{dict(labels)} sum was {total}, expected >= {minimum}"
                )
            return
    raise RuntimeError(f"{url}: none of {names} appeared with labels {dict(labels)}")


def _check_scrape(
    result: TaskResult,
    *,
    url: object,
    expect: tuple[str, ...],
    reject: tuple[str, ...],
) -> None:
    for sample in expect:
        if sample not in result.stdout:
            raise RuntimeError(f"{url}: expected sample not scraped: {sample}")
    for sample in reject:
        if sample in result.stdout:
            raise RuntimeError(f"{url}: sample present and should not be: {sample}")


def _check_minimums(
    result: TaskResult,
    *,
    url: object,
    minimums: tuple[tuple[str, Mapping[str, str], float], ...],
    any_minimums: tuple[tuple[tuple[str, ...], Mapping[str, str], float], ...],
) -> None:
    for name, labels, minimum in minimums:
        _require_metric_sum(url, result.stdout, (name,), labels, minimum)
    for names, labels, minimum in any_minimums:
        _require_metric_sum(url, result.stdout, names, labels, minimum)


class PrometheusScrapeCheckTask(CommandTask):
    def __init__(
        self,
        *,
        url: MetricsEndpoint,
        executor: CommandTaskExecutor,
        role: str = "host",
        expect: tuple[str, ...] = (),
        reject: tuple[str, ...] = (),
        options: CommandOptions | None = None,
        title: str | None = None,
        semantic_key: str | None = None,
    ) -> None:
        if not expect and not reject:
            raise ValueError("a scrape check must expect or reject at least one sample")
        if callable(url) and not semantic_key:
            raise ValueError("semantic_key is required for a dynamic metrics endpoint")

        argv = (
            (lambda inputs: ("curl", "-fsS", _resolve(url, inputs)))
            if callable(url)
            else ("curl", "-fsS", url)
        )
        key = build_semantic_key(
            "metrics-scrape:v2",
            {"endpoint": semantic_key or url, "expect": expect, "reject": reject},
        )
        super().__init__(
            title=title or f"Check the metrics at {url}",
            argv=argv,
            executor=executor,
            role=role,
            options=options,
            verify=partial(_check_scrape, url=url, expect=expect, reject=reject),
            semantic_key=key,
        )


class PrometheusMinimumCheckTask(CommandTask):
    def __init__(
        self,
        *,
        url: MetricsEndpoint,
        minimums: tuple[tuple[str, Mapping[str, str], float], ...],
        any_minimums: tuple[tuple[tuple[str, ...], Mapping[str, str], float], ...] = (),
        executor: CommandTaskExecutor,
        role: str = "host",
        options: CommandOptions | None = None,
        title: str | None = None,
        semantic_key: str | None = None,
    ) -> None:
        if not minimums and not any_minimums:
            raise ValueError("a metric minimum check needs at least one expectation")
        if callable(url) and not semantic_key:
            raise ValueError("semantic_key is required for a dynamic metrics endpoint")

        argv = (
            (lambda inputs: ("curl", "-fsS", _resolve(url, inputs)))
            if callable(url)
            else ("curl", "-fsS", url)
        )
        key = build_semantic_key(
            "metrics-minimum:v2",
            {
                "endpoint": semantic_key or url,
                "minimums": minimums,
                "any_minimums": any_minimums,
            },
        )
        super().__init__(
            title=title or "Check metric values",
            argv=argv,
            executor=executor,
            role=role,
            options=options,
            verify=partial(
                _check_minimums,
                url=url,
                minimums=minimums,
                any_minimums=any_minimums,
            ),
            semantic_key=key,
        )
