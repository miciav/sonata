from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
from sonata_engine import TaskInputs, Workflow

from sonata_tasks.ansible import AnsiblePlaybookTask
from sonata_tasks.command import CommandTask
from sonata_tasks.compose import DestroyDockerCompose, DockerComposeProject, docker_compose_resource
from sonata_tasks.cosign import CosignTask
from sonata_tasks.docker import DockerBuildTask, DockerInspectTask, DockerPushTask, DockerTask
from sonata_tasks.execution.models import CommandOptions, TaskResult
from sonata_tasks.gradle import GradleTask
from sonata_tasks.helm import HelmInstallTask, HelmReleaseSpec
from sonata_tasks.http import HttpStatusCheckTask
from sonata_tasks.imagetools import ImagetoolsCreateTask
from sonata_tasks.k6 import K6Task, k6_argv
from sonata_tasks.k6_models import K6Config, K6Stage
from sonata_tasks.kubectl import KubectlTask
from sonata_tasks.metrics import PrometheusMinimumCheckTask, metric_sum
from sonata_tasks.prometheus import HttpPrometheusClient, PrometheusRetryPolicy
from sonata_tasks.skopeo import SkopeoCopyTask
from sonata_tasks.syft import SyftTask
from sonata_tasks.testing import RecordingExecutor


def _run(task: CommandTask, executor: RecordingExecutor) -> tuple[str, ...]:
    _ = task.run(TaskInputs.empty())
    return executor.seen[-1].argv


def test_docker_wrappers_are_direct_commands_and_preserve_shell_characters() -> None:
    executor = RecordingExecutor()
    options = CommandOptions(cwd=Path("/tmp/a path"), env={"TOKEN": "a $b"}, timeout_seconds=4)
    tasks = (
        DockerTask("logs", "name with spaces", executor=executor, options=options),
        DockerBuildTask(
            image="reg/a:b",
            dockerfile="Docker file",
            context=".",
            executor=executor,
            options=options,
        ),
        DockerPushTask(image="reg/a:b", executor=executor, options=options),
        DockerInspectTask(container="name;still-one-arg", executor=executor, options=options),
    )
    for task in tasks:
        _ = task.run(TaskInputs.empty())
        assert executor.seen[-1].options == options
        assert type(task).__bases__ == (CommandTask,)
    assert executor.seen[0].argv[-1] == "name with spaces"


def test_compose_resource_does_not_clear_on_acquire_and_cleanup_defaults_are_safe() -> None:
    executor = RecordingExecutor()
    project = DockerComposeProject("other-app", Path("compose file.yaml"), "https://ready")
    resource = docker_compose_resource(project, executor=executor)
    workflow = Workflow("compose")
    workflow.add(CommandTask(title="use", argv=("true",), executor=executor), requires=(resource,))
    workflow.run()
    commands = [spec.argv for spec in executor.seen]
    assert [command[0] for command in commands[:2]] == ["docker", "curl"]
    assert commands[-1][-1] == "down"
    assert "--volumes" not in commands[-1]
    assert "--remove-orphans" not in commands[-1]


def test_compose_cleanup_flags_are_explicit() -> None:
    executor = RecordingExecutor()
    task = DestroyDockerCompose(
        DockerComposeProject("app", Path("c.yml"), "https://ready"),
        executor=executor,
        remove_volumes=True,
        remove_orphans=True,
    )
    assert _run(task, executor)[-2:] == ("--volumes", "--remove-orphans")


def test_helm_tool_timeout_and_process_timeout_are_independent() -> None:
    executor = RecordingExecutor()
    options = CommandOptions(timeout_seconds=600)
    spec = HelmReleaseSpec("app", "chart", "ns", (), timeout="12m")
    argv = _run(HelmInstallTask(spec, executor=executor, options=options), executor)
    assert argv[argv.index("--timeout") + 1] == "12m"
    assert executor.seen[0].options.timeout_seconds == 600


def test_gradle_daemon_is_an_explicit_tool_option() -> None:
    executor = RecordingExecutor()
    assert _run(GradleTask("build", executor=executor), executor)[-1] == "--no-daemon"
    assert _run(GradleTask("build", executor=executor, daemon=True), executor)[-1] == "--daemon"


def test_other_tool_wrappers_forward_options_and_policy_parameters() -> None:
    executor = RecordingExecutor()
    options = CommandOptions(env={"A": "value with spaces"})
    assert _run(
        KubectlTask("get", "pods", executor=executor, namespace="tenant", options=options), executor
    )[:4] == ("kubectl", "-n", "tenant", "get")
    assert "--src-tls-verify=false" in _run(
        SkopeoCopyTask(
            source="a",
            destination="b",
            authfile="auth file",
            executor=executor,
            src_tls_verify=False,
            options=options,
        ),
        executor,
    )
    syft = _run(
        SyftTask(
            image="a",
            output_path="/tmp/result.json",
            docker_config="/auth",
            output_format="cyclonedx-json",
            tool_image="syft:test",
            executor=executor,
            options=options,
        ),
        executor,
    )
    assert "syft:test" in syft and "cyclonedx-json=/out/result.json" in syft
    cosign = _run(
        CosignTask(
            operation="attest",
            image="a",
            key_file="key",
            password_file="password",
            docker_config="auth",
            predicate_file="statement",
            predicate_type="https://example/type",
            tool_image="cosign:test",
            executor=executor,
            options=options,
        ),
        executor,
    )
    assert "cosign:test" in cosign and "https://example/type" in cosign


def test_imagetools_merges_docker_config_without_losing_options() -> None:
    executor = RecordingExecutor()
    task = ImagetoolsCreateTask(
        tag="a",
        sources=("b",),
        docker_config="/auth",
        executor=executor,
        options=CommandOptions(env={"KEEP": "yes"}, timeout_seconds=3),
    )
    _ = task.run(TaskInputs.empty())
    assert dict(executor.seen[0].options.env) == {"KEEP": "yes", "DOCKER_CONFIG": "/auth"}
    assert executor.seen[0].options.timeout_seconds == 3


def test_k6_is_application_neutral_and_retains_threshold_failure() -> None:
    config = K6Config(
        Path("scenario.js"),
        Path("summary.json"),
        stages=(K6Stage("5s", 2),),
        env={"TARGET_URL": "https://other"},
    )
    argv = k6_argv(config)
    assert "TARGET_URL=https://other" in argv
    assert all("NANOFAAS" not in arg for arg in argv)
    executor = RecordingExecutor(results=[TaskResult("", "passed", 99, frozenset({0, 99}))])
    outcome = K6Task(config, executor=executor).run(TaskInputs.empty())
    assert outcome.value is not None and outcome.value.passed is False
    assert executor.seen[0].options.expected_exit_codes == frozenset({0, 99})


def test_http_status_supports_headers_empty_body_and_expected_error_status() -> None:
    executor = RecordingExecutor(results=[TaskResult("", "passed", 0, stdout="502\n")])
    task = HttpStatusCheckTask(
        url="https://example/status",
        expected_status=502,
        headers={"Content-Type": "text/plain"},
        payload="",
        executor=executor,
    )
    argv = _run(task, executor)
    assert ("-H", "Content-Type: text/plain") == argv[6:8]
    assert argv[8:10] == ("--data", "")


def test_metrics_use_the_complete_endpoint_and_unrelated_labels() -> None:
    scrape = 'job_requests_total{tenant="acme"} 4\njob_requests_total{tenant="other"} 9\n'
    assert metric_sum(scrape, "job_requests_total", {"tenant": "acme"}) == (1, 4.0)
    executor = RecordingExecutor(results=[TaskResult("", "passed", 0, stdout=scrape)])
    url = "https://metrics.example:9443/custom/metrics"
    task = PrometheusMinimumCheckTask(
        url=url, minimums=(("job_requests_total", {"tenant": "acme"}, 3),), executor=executor
    )
    _ = task.run(TaskInputs.empty())
    assert executor.seen[0].argv[-1] == url


def test_prometheus_preserves_series_and_retries_only_transport_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectTimeout("late", request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {"metric": {"tenant": "a"}, "values": [[1, "2"]]},
                        {"metric": {"tenant": "b"}, "values": [[1, "3"]]},
                    ],
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prometheus = HttpPrometheusClient(
        "https://metrics", client=client, retry_policy=PrometheusRetryPolicy(2, 0)
    )
    now = datetime.now(timezone.utc)
    series = prometheus.query_range("jobs", now, now)
    assert calls == 2
    assert [dict(item.labels) for item in series] == [{"tenant": "a"}, {"tenant": "b"}]


def test_ansible_task_has_no_vm_or_product_assumptions(tmp_path: Path) -> None:
    playbook = tmp_path / "configure other app.yml"
    playbook.write_text("---\n", encoding="utf-8")
    executor = RecordingExecutor()
    task = AnsiblePlaybookTask(
        playbook=playbook,
        inventory="host.example,",
        user="deploy",
        private_key_path=tmp_path / "a key",
        extra_vars={"name": "a b"},
        ansible_config=tmp_path / "ansible.cfg",
        executor=executor,
        options=CommandOptions(cwd=tmp_path, env={"KEEP": "yes"}),
    )
    argv = _run(task, executor)
    assert argv[-1] == str(playbook.absolute())
    assert "name=a b" in argv
    assert dict(executor.seen[0].options.env) == {
        "KEEP": "yes",
        "ANSIBLE_CONFIG": str(tmp_path / "ansible.cfg"),
    }
