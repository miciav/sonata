from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

import httpx


@dataclass(frozen=True, slots=True)
class PrometheusSample:
    timestamp: float
    value: float


@dataclass(frozen=True, slots=True)
class PrometheusSeries:
    labels: Mapping[str, str]
    samples: tuple[PrometheusSample, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))
        object.__setattr__(self, "samples", tuple(self.samples))


@dataclass(frozen=True, slots=True)
class PrometheusRetryPolicy:
    attempts: int = 1
    backoff_seconds: float = 0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least one")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")


class HttpClient(Protocol):
    def get(self, url: str, *, params: Mapping[str, str], timeout: float) -> httpx.Response: ...


class HttpPrometheusClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 20,
        retry_policy: PrometheusRetryPolicy = PrometheusRetryPolicy(),
        client: HttpClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._retry = retry_policy
        self._client: HttpClient = client or httpx.Client()
        self._sleep = sleep

    def _get(self, path: str, params: Mapping[str, str]) -> object:
        response: httpx.Response | None = None
        for attempt in range(1, self._retry.attempts + 1):
            try:
                response = self._client.get(
                    f"{self._base_url}{path}", params=params, timeout=self._timeout
                )
            except httpx.TransportError as error:
                if attempt == self._retry.attempts:
                    raise RuntimeError(
                        f"prometheus transport failed for {path}: {error}"
                    ) from error
                self._sleep(self._retry.backoff_seconds)
                continue
            break
        assert response is not None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"prometheus HTTP {response.status_code} for {path}") from error
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(f"prometheus returned invalid JSON for {path}") from error
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise RuntimeError(f"prometheus query failed for {path}: {payload}")
        if "data" not in payload:
            raise RuntimeError(f"prometheus response has no data for {path}")
        return payload["data"]

    def query_range(
        self, expr: str, start: datetime, end: datetime, step_seconds: int = 2
    ) -> tuple[PrometheusSeries, ...]:
        data = self._get(
            "/api/v1/query_range",
            {
                "query": expr,
                "start": str(start.timestamp()),
                "end": str(end.timestamp()),
                "step": f"{step_seconds}s",
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise RuntimeError("invalid prometheus query_range payload")
        series: list[PrometheusSeries] = []
        for raw_series in data["result"]:
            if not isinstance(raw_series, dict):
                raise RuntimeError("invalid prometheus series")
            labels, values = raw_series.get("metric"), raw_series.get("values")
            if (
                not isinstance(labels, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items())
                or not isinstance(values, list)
            ):
                raise RuntimeError("invalid prometheus series")
            samples: list[PrometheusSample] = []
            for sample in values:
                if not isinstance(sample, list) or len(sample) != 2:
                    raise RuntimeError("invalid prometheus sample")
                try:
                    samples.append(PrometheusSample(float(sample[0]), float(sample[1])))
                except (TypeError, ValueError) as error:
                    raise RuntimeError(f"invalid prometheus sample: {sample!r}") from error
            series.append(PrometheusSeries(labels, tuple(samples)))
        return tuple(series)

    def server_time(self) -> float:
        data = self._get("/api/v1/query", {"query": "time()"})
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, list) and len(result) == 2:
            try:
                return float(result[1])
            except (TypeError, ValueError) as error:
                raise RuntimeError(f"invalid prometheus time() value: {result!r}") from error
        raise RuntimeError(f"unexpected prometheus time() result: {result!r}")
