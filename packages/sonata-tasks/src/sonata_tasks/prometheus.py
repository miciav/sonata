"""Query the Prometheus HTTP API over an injectable HTTP client."""

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
    """One point in a series: a Unix timestamp and the value recorded there."""

    timestamp: float
    value: float


@dataclass(frozen=True, slots=True)
class PrometheusSeries:
    """The samples returned for one set of labels."""

    labels: Mapping[str, str]
    samples: tuple[PrometheusSample, ...]

    def __post_init__(self) -> None:
        """Freeze ``labels`` into a read-only mapping and ``samples`` into a tuple."""
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))
        object.__setattr__(self, "samples", tuple(self.samples))


@dataclass(frozen=True, slots=True)
class PrometheusRetryPolicy:
    """How often and how long to retry a failed Prometheus request."""

    attempts: int = 1
    backoff_seconds: float = 0

    def __post_init__(self) -> None:
        """Reject counts and delays that would not make sense when retrying.

        Raises:
            ValueError: If ``attempts`` is below one or ``backoff_seconds`` is
                negative.

        """
        if self.attempts < 1:
            raise ValueError("attempts must be at least one")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")


# Shared default for callers that pass no policy. The policy is frozen, so one
# instance is safe to hand to every client instead of rebuilding it per call.
_DEFAULT_RETRY_POLICY = PrometheusRetryPolicy()


class HttpClient(Protocol):
    """The slice of the httpx client the Prometheus client depends on."""

    def get(
        self, url: str, *, params: Mapping[str, str], timeout: float
    ) -> httpx.Response:
        """Issue a GET request and return its response."""
        ...


def _parse_sample(sample: object) -> PrometheusSample:
    if not isinstance(sample, list) or len(sample) != 2:
        raise RuntimeError("invalid prometheus sample")
    try:
        return PrometheusSample(float(sample[0]), float(sample[1]))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"invalid prometheus sample: {sample!r}") from error


def _parse_series(raw_series: object) -> PrometheusSeries:
    if not isinstance(raw_series, dict):
        raise RuntimeError("invalid prometheus series")
    labels, values = raw_series.get("metric"), raw_series.get("values")
    if (
        not isinstance(labels, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in labels.items()
        )
        or not isinstance(values, list)
    ):
        raise RuntimeError("invalid prometheus series")
    return PrometheusSeries(labels, tuple(_parse_sample(sample) for sample in values))


class HttpPrometheusClient:
    """Read Prometheus series over HTTP, retrying transport failures."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 20,
        retry_policy: PrometheusRetryPolicy = _DEFAULT_RETRY_POLICY,
        client: HttpClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Configure the client against ``base_url``.

        A trailing slash is stripped so paths can be appended directly.
        ``retry_policy`` governs transport retries, ``client`` defaults to a new
        :class:`httpx.Client`, and ``sleep`` is the wait used between retries.
        """
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
        # Narrows an Optional for the type checker; it is not a runtime guard.
        # The loop above can only leave with `response` assigned (it breaks) or
        # by raising, and PrometheusRetryPolicy rejects a zero attempt count, so
        # this cannot be reached with None. A raise here would be dead code.
        assert response is not None  # nosec B101
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"prometheus HTTP {response.status_code} for {path}"
            ) from error
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(
                f"prometheus returned invalid JSON for {path}"
            ) from error
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise RuntimeError(f"prometheus query failed for {path}: {payload}")
        if "data" not in payload:
            raise RuntimeError(f"prometheus response has no data for {path}")
        return payload["data"]

    def query_range(
        self, expr: str, start: datetime, end: datetime, step_seconds: int = 2
    ) -> tuple[PrometheusSeries, ...]:
        """Run a range query and return the series it matched.

        ``step_seconds`` is the resolution between points. Raises
        ``RuntimeError`` if the response is not a usable query_range payload.
        """
        data = self._get(
            "/api/v1/query_range",
            {
                "query": expr,
                "start": str(start.timestamp()),
                "end": str(end.timestamp()),
                "step": f"{step_seconds}s",
            },
        )
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, list):
            raise RuntimeError("invalid prometheus query_range payload")
        return tuple(_parse_series(raw_series) for raw_series in result)

    def server_time(self) -> float:
        """Return the Prometheus server's own clock, as a Unix timestamp.

        Reading the server's time rather than the local one keeps a range query
        anchored to the clock that recorded the samples.
        """
        data = self._get("/api/v1/query", {"query": "time()"})
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, list) and len(result) == 2:
            try:
                return float(result[1])
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    f"invalid prometheus time() value: {result!r}"
                ) from error
        raise RuntimeError(f"unexpected prometheus time() result: {result!r}")
