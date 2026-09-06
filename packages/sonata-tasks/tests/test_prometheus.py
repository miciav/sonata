from datetime import datetime, timezone

import httpx
import pytest
from sonata_tasks.prometheus import HttpPrometheusClient, PrometheusRetryPolicy


class FakeHttpClient:
    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, object, float]] = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", "http://prometheus/api"),
    )


def _range_payload() -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "result": [
                {"metric": {"tenant": "one"}, "values": [[1, "2.5"], [2, "3"]]},
                {"metric": {"tenant": "two"}, "values": [[1, "4"]]},
            ]
        },
    }


def test_query_range_preserves_series_labels_and_samples() -> None:
    transport = FakeHttpClient(_response(_range_payload()))
    client = HttpPrometheusClient("http://prometheus/", client=transport)

    series = client.query_range(
        "requests_total",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        5,
    )

    assert [dict(item.labels) for item in series] == [{"tenant": "one"}, {"tenant": "two"}]
    assert [(sample.timestamp, sample.value) for sample in series[0].samples] == [
        (1.0, 2.5),
        (2.0, 3.0),
    ]
    assert transport.calls[0][0] == "http://prometheus/api/v1/query_range"


def test_transport_errors_retry_only_as_configured() -> None:
    sleeps: list[float] = []
    transport = FakeHttpClient(
        httpx.ConnectError("refused"),
        _response(_range_payload()),
    )
    client = HttpPrometheusClient(
        "http://prometheus",
        client=transport,
        retry_policy=PrometheusRetryPolicy(attempts=2, backoff_seconds=0.25),
        sleep=sleeps.append,
    )

    assert (
        len(client.query_range("up", datetime.now(timezone.utc), datetime.now(timezone.utc))) == 2
    )
    assert sleeps == [0.25]


def test_exhausted_transport_errors_are_reported() -> None:
    client = HttpPrometheusClient(
        "http://prometheus",
        client=FakeHttpClient(httpx.ConnectError("refused")),
    )

    with pytest.raises(RuntimeError, match="transport failed"):
        client.server_time()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response({}, status=503), "HTTP 503"),
        (
            httpx.Response(
                200,
                content=b"not-json",
                request=httpx.Request("GET", "http://prometheus/api"),
            ),
            "invalid JSON",
        ),
        (_response({"status": "error", "error": "bad query"}), "query failed"),
        (_response({"status": "success"}), "no data"),
    ],
)
def test_protocol_failures_remain_distinct(response: httpx.Response, message: str) -> None:
    client = HttpPrometheusClient("http://prometheus", client=FakeHttpClient(response))

    with pytest.raises(RuntimeError, match=message):
        client.server_time()


@pytest.mark.parametrize(
    "data",
    [
        {"result": "wrong"},
        {"result": ["wrong"]},
        {"result": [{"metric": [], "values": []}]},
        {"result": [{"metric": {}, "values": ["wrong"]}]},
        {"result": [{"metric": {}, "values": [[1, "not-a-number"]]}]},
    ],
)
def test_query_range_rejects_malformed_series(data: object) -> None:
    client = HttpPrometheusClient(
        "http://prometheus",
        client=FakeHttpClient(_response({"status": "success", "data": data})),
    )

    with pytest.raises(RuntimeError, match="invalid prometheus"):
        client.query_range("up", datetime.now(timezone.utc), datetime.now(timezone.utc))


def test_server_time_parses_a_scalar() -> None:
    client = HttpPrometheusClient(
        "http://prometheus",
        client=FakeHttpClient(
            _response({"status": "success", "data": {"result": [1, "1780728785.1"]}})
        ),
    )

    assert client.server_time() == 1780728785.1


@pytest.mark.parametrize("result", [None, [], [1], [1, "bad"]])
def test_server_time_rejects_an_invalid_scalar(result: object) -> None:
    client = HttpPrometheusClient(
        "http://prometheus",
        client=FakeHttpClient(_response({"status": "success", "data": {"result": result}})),
    )

    with pytest.raises(RuntimeError, match="prometheus time|unexpected prometheus"):
        client.server_time()


def test_retry_policy_validates_its_limits() -> None:
    with pytest.raises(ValueError, match="at least one"):
        PrometheusRetryPolicy(attempts=0)
    with pytest.raises(ValueError, match="negative"):
        PrometheusRetryPolicy(backoff_seconds=-1)
