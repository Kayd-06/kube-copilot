from datetime import datetime, timezone

from kubernetes.client import (
    ApiException,
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1ObjectMeta,
    V1Pod,
    V1PodCondition,
    V1PodSpec,
    V1PodStatus,
)

from app.k8s_client import (
    K8sClient,
    K8sUnavailableError,
    PodNotFoundError,
    diagnose_reason,
    to_pod_summary,
)


def _pod(
    *,
    name="app-0",
    namespace="default",
    phase="Running",
    reason=None,
    message=None,
    containers=None,
    conditions=None,
    init_containers=None,
):
    return V1Pod(
        metadata=V1ObjectMeta(
            name=name,
            namespace=namespace,
            creation_timestamp=datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc),
        ),
        status=V1PodStatus(
            phase=phase,
            reason=reason,
            message=message,
            container_statuses=containers,
            init_container_statuses=init_containers,
            conditions=conditions,
            qos_class="BestEffort",
        ),
        spec=V1PodSpec(node_name="node-1", containers=[]),
    )


def _waiting(name, reason, message="", restart_count=0, last_terminated=None):
    last = None
    if last_terminated is not None:
        term_reason, exit_code = last_terminated
        last = V1ContainerState(
            terminated=V1ContainerStateTerminated(reason=term_reason, exit_code=exit_code)
        )
    return V1ContainerStatus(
        name=name,
        ready=False,
        restart_count=restart_count,
        image="busybox:1.36",
        image_id="",
        container_id="ctr://abc",
        state=V1ContainerState(waiting=V1ContainerStateWaiting(reason=reason, message=message)),
        last_state=last,
    )


def test_crashloop_is_unhealthy():
    pod = _pod(containers=[_waiting("crash", "CrashLoopBackOff", restart_count=5)])
    unhealthy, reason, _ = diagnose_reason(pod)
    assert unhealthy is True
    assert reason == "CrashLoopBackOff"


def test_oomkilled_preferred_over_crashloop():
    pod = _pod(
        containers=[
            _waiting(
                "hungry",
                "CrashLoopBackOff",
                restart_count=4,
                last_terminated=("OOMKilled", 137),
            )
        ]
    )
    unhealthy, reason, _ = diagnose_reason(pod)
    assert unhealthy is True
    assert reason == "OOMKilled"


def test_image_pull_backoff_is_unhealthy():
    pod = _pod(
        phase="Pending",
        containers=[_waiting("main", "ImagePullBackOff", "Back-off pulling image")],
    )
    unhealthy, reason, _ = diagnose_reason(pod)
    assert unhealthy is True
    assert reason == "ImagePullBackOff"


def test_pending_unschedulable_is_unhealthy():
    pod = _pod(
        phase="Pending",
        conditions=[
            V1PodCondition(
                type="PodScheduled",
                status="False",
                reason="Unschedulable",
                message="0/1 nodes are available: insufficient cpu",
            )
        ],
    )
    unhealthy, reason, message = diagnose_reason(pod)
    assert unhealthy is True
    assert reason == "Unschedulable"
    assert "insufficient cpu" in (message or "")


def test_failed_phase_is_unhealthy():
    pod = _pod(phase="Failed", reason="Evicted", message="The node was low on resource: memory.")
    unhealthy, reason, _ = diagnose_reason(pod)
    assert unhealthy is True
    assert reason == "Evicted"


def test_healthy_running_pod_is_not_flagged():
    running = V1ContainerStatus(
        name="app",
        ready=True,
        restart_count=0,
        image="busybox:1.36",
        image_id="sha256:abc",
        container_id="ctr://xyz",
        state=V1ContainerState(
            running=V1ContainerStateRunning(started_at=datetime(2026, 11, 12, tzinfo=timezone.utc))
        ),
    )
    pod = _pod(phase="Running", containers=[running])
    unhealthy, _, _ = diagnose_reason(pod)
    assert unhealthy is False


def test_to_pod_summary_maps_fields():
    pod = _pod(
        name="web-1",
        namespace="prod",
        containers=[_waiting("web", "CrashLoopBackOff", "boom", restart_count=3)],
    )
    summary = to_pod_summary(pod)
    assert summary.name == "web-1"
    assert summary.namespace == "prod"
    assert summary.reason == "CrashLoopBackOff"
    assert summary.restart_count == 3
    assert summary.ready is False
    assert summary.node == "node-1"
    assert len(summary.containers) == 1
    assert summary.containers[0].name == "web"


def test_list_unhealthy_filters_healthy(monkeypatch):
    healthy = _pod(name="ok", containers=[])
    # No statuses and Running/empty -> not unhealthy
    healthy.status.phase = "Running"
    crash = _pod(name="bad", containers=[_waiting("c", "CrashLoopBackOff", restart_count=1)])

    class FakeList:
        items = [healthy, crash]

    class FakeApi:
        def list_pod_for_all_namespaces(self):
            return FakeList()

    k8s = K8sClient.__new__(K8sClient)
    k8s._settings = None
    k8s._loaded = True
    k8s._core = FakeApi()

    results = k8s.list_unhealthy_pods()
    assert [p.name for p in results] == ["bad"]


def test_get_pod_not_found():
    class FakeApi:
        def read_namespaced_pod(self, name, namespace):
            raise ApiException(status=404, reason="Not Found")

    k8s = K8sClient.__new__(K8sClient)
    k8s._settings = None
    k8s._loaded = True
    k8s._core = FakeApi()

    try:
        k8s.get_pod("ns", "missing")
        assert False, "expected PodNotFoundError"
    except PodNotFoundError as exc:
        assert exc.namespace == "ns"
        assert exc.name == "missing"


def test_get_pod_logs_fetches_previous_on_restart():
    calls = []

    class FakeApi:
        def read_namespaced_pod_log(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("previous"):
                return "old crash log"
            return "current waiting"

    crash = _pod(
        name="p",
        namespace="ns",
        containers=[_waiting("crash", "CrashLoopBackOff", restart_count=2)],
    )
    k8s = K8sClient.__new__(K8sClient)

    class Settings:
        log_tail_lines = 200

    k8s._settings = Settings()
    k8s._loaded = True
    k8s._core = FakeApi()

    logs = k8s.get_pod_logs("ns", "p", pod=crash)
    assert logs.current == "current waiting"
    assert logs.previous == "old crash log"
    assert logs.container == "crash"
    assert any(c.get("previous") is True for c in calls)
    assert all(c.get("tail_lines") == 200 for c in calls)


def test_ensure_connected_raises_unavailable(monkeypatch):
    from app.config import Settings

    k8s = K8sClient(settings=Settings(k8s_kubeconfig="/no/such/kubeconfig"))

    def boom(*args, **kwargs):
        raise Exception("config not found")

    monkeypatch.setattr("app.k8s_client.config.load_kube_config", boom)
    try:
        k8s._ensure_connected()
        assert False, "expected K8sUnavailableError"
    except K8sUnavailableError as exc:
        assert "Could not load Kubernetes configuration" in str(exc)
