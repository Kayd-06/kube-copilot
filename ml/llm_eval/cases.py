"""Hand-labelled fault scenarios for the LLM evaluation, reproduced on the kind cluster.

Every label here (category, acceptable remediations, matching runbooks) was
written from the *injected fault*, before any model saw the case. The
workloads are tiny busybox/nginx/curl containers whose scripts behave like a
small service; everything Kubernetes reports about them (pod status, events,
restarts, OOM kills, scheduling, probe results, lifecycle signals) is real.

Namespaces and workload names are neutral (`kc-eval-07`, `payments`, ...) so
nothing in the context leaks the label.

`rollout` cases first deploy a healthy revision, wait for it, then apply a
broken revision, so the Deployment has a healthy previous revision and
`rollback_deployment` is the right fix. `first` cases are broken on revision
1, so no allowed action fixes them and the right proposal is `no_action`.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

BUSYBOX = "busybox:1.36"
NGINX = "nginx:1.27-alpine"
CURL = "curlimages/curl:8.11.1"

Obj = dict[str, Any]
PodPredicate = Callable[[dict], bool]


# ----------------------------------------------------------------- scripts
def serve_loop(service: str, version: str = "1.4.2") -> str:
    return (
        f'echo "$(date -Iseconds) INFO starting {service} v{version}"; '
        'echo "$(date -Iseconds) INFO listening on :8080"; '
        "while true; do "
        'echo "$(date -Iseconds) INFO handled request path=/api/v1/items status=200 latency_ms=$((RANDOM%40+5))"; '
        "sleep 3; done"
    )


def heredoc_then_exit(lines: list[str], code: int, pre: str = "") -> str:
    body = "\n".join(lines)
    return f"{pre}cat <<'EOF'\n{body}\nEOF\nexit {code}"


# ----------------------------------------------------------------- builders
def container(
    name: str,
    image: str = BUSYBOX,
    script: str | None = None,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    env_extra: list[Obj] | None = None,
    env_from: list[Obj] | None = None,
    resources: Obj | None = None,
    liveness: Obj | None = None,
    readiness: Obj | None = None,
    mounts: list[Obj] | None = None,
    port: int | None = None,
) -> Obj:
    c: Obj = {"name": name, "image": image, "imagePullPolicy": "IfNotPresent"}
    if script is not None:
        c["command"] = ["sh", "-c", script]
    elif command is not None:
        c["command"] = command
    env_list = [{"name": k, "value": v} for k, v in (env or {}).items()] + list(env_extra or [])
    if env_list:
        c["env"] = env_list
    if env_from:
        c["envFrom"] = env_from
    c["resources"] = resources or {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"memory": "64Mi"}}
    if liveness:
        c["livenessProbe"] = liveness
    if readiness:
        c["readinessProbe"] = readiness
    if mounts:
        c["volumeMounts"] = mounts
    if port:
        c["ports"] = [{"containerPort": port}]
    return c


def deployment(
    name: str,
    containers: list[Obj],
    replicas: int = 1,
    init: list[Obj] | None = None,
    volumes: list[Obj] | None = None,
    node_selector: dict[str, str] | None = None,
    affinity: Obj | None = None,
) -> Obj:
    spec: Obj = {"containers": containers, "terminationGracePeriodSeconds": 2}
    if init:
        spec["initContainers"] = init
    if volumes:
        spec["volumes"] = volumes
    if node_selector:
        spec["nodeSelector"] = node_selector
    if affinity:
        spec["affinity"] = affinity
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "labels": {"app": name}},
        "spec": {
            "replicas": replicas,
            "revisionHistoryLimit": 5,
            "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name}}, "spec": spec},
        },
    }


def configmap(name: str, data: dict[str, str]) -> Obj:
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name}, "data": data}


def secret(name: str, data: dict[str, str]) -> Obj:
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name}, "stringData": data}


def service(name: str, app: str, port: int, target_port: int) -> Obj:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name},
        "spec": {"selector": {"app": app}, "ports": [{"port": port, "targetPort": target_port}]},
    }


def http_probe(path: str = "/", port: int = 80, delay: int = 3, period: int = 5, failures: int = 3) -> Obj:
    return {
        "httpGet": {"path": path, "port": port},
        "initialDelaySeconds": delay,
        "periodSeconds": period,
        "failureThreshold": failures,
        "timeoutSeconds": 2,
    }


def mutate(obj: Obj, fn: Callable[[Obj], None]) -> Obj:
    out = copy.deepcopy(obj)
    fn(out)
    return out


def pod_spec(dep: Obj) -> Obj:
    return dep["spec"]["template"]["spec"]


# ----------------------------------------------------------------- predicates
def _statuses(pod: dict, init: bool = True) -> list[dict]:
    st = pod.get("status", {})
    return list(st.get("containerStatuses") or []) + (list(st.get("initContainerStatuses") or []) if init else [])


def waiting(*reasons: str) -> PodPredicate:
    def check(pod: dict) -> bool:
        return any((cs.get("state", {}).get("waiting") or {}).get("reason") in reasons for cs in _statuses(pod))

    return check


def restarts_at_least(n: int) -> PodPredicate:
    return lambda pod: any(cs.get("restartCount", 0) >= n for cs in _statuses(pod))


def last_terminated(reason: str) -> PodPredicate:
    def check(pod: dict) -> bool:
        return any(
            (cs.get("lastState", {}).get("terminated") or {}).get("reason") == reason
            or (cs.get("state", {}).get("terminated") or {}).get("reason") == reason
            for cs in _statuses(pod)
        )

    return check


def unscheduled(pod: dict) -> bool:
    conds = pod.get("status", {}).get("conditions") or []
    return pod.get("status", {}).get("phase") == "Pending" and any(
        c.get("type") == "PodScheduled" and c.get("status") == "False" for c in conds
    )


def pending(pod: dict) -> bool:
    return pod.get("status", {}).get("phase") == "Pending"


def running_not_ready(pod: dict) -> bool:
    st = pod.get("status", {})
    cs = st.get("containerStatuses") or []
    return st.get("phase") == "Running" and bool(cs) and all("running" in c.get("state", {}) for c in cs) and not all(
        c.get("ready") for c in cs
    )


def ready(pod: dict) -> bool:
    cs = pod.get("status", {}).get("containerStatuses") or []
    return bool(cs) and all(c.get("ready") for c in cs)


def succeeded(pod: dict) -> bool:
    return pod.get("status", {}).get("phase") == "Succeeded"


def all_of(*preds: PodPredicate) -> PodPredicate:
    return lambda pod: all(p(pod) for p in preds)


# ----------------------------------------------------------------- the case type
@dataclass
class Case:
    id: str
    title: str
    category: str
    expected_actions: list[str]
    fault: str
    workload: str
    base: list[Obj]
    expect: PodPredicate
    rollout: list[Obj] = field(default_factory=list)  # applied after `base` is healthy
    expected_runbooks: list[str] = field(default_factory=list)
    difficulty: str = "easy"  # easy | medium | hard
    settle_s: int = 75  # after the fault is visible, before capture
    hook: str | None = None  # special injection handled by capture.py
    post: list[list[str]] = field(default_factory=list)  # kubectl args run after a healthy rollout
    notes: str = ""

    def labels(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "expected_actions": list(self.expected_actions),
            "expected_runbooks": list(self.expected_runbooks),
            "difficulty": self.difficulty,
            "fault": self.fault,
            "notes": self.notes,
        }


def _healthy(name: str, **kw: Any) -> Obj:
    return deployment(name, [container(name, script=serve_loop(name), **kw)])


def _nginx(name: str, replicas: int = 1, **kw: Any) -> Obj:
    return deployment(
        name,
        [container(name, image=NGINX, port=80, resources={"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"memory": "64Mi"}}, **kw)],
        replicas=replicas,
    )


def _set_image(image: str) -> Callable[[Obj], None]:
    def fn(d: Obj) -> None:
        pod_spec(d)["containers"][0]["image"] = image

    return fn


def _set(path_fn: Callable[[Obj], None]) -> Callable[[Obj], None]:
    return path_fn


# ----------------------------------------------------------------- cases
def build_cases() -> list[Case]:
    cases: list[Case] = []
    add = cases.append

    # --- image pull --------------------------------------------------------
    checkout = _nginx("checkout")
    add(Case(
        "img_tag_rollout", "Rollout to a nginx tag that does not exist", "image_pull", ["rollback_deployment"],
        "Deployment updated from nginx:1.27-alpine to the non-existent tag nginx:1.27-alpine-typo.",
        "checkout", [checkout], waiting("ErrImagePull", "ImagePullBackOff"),
        rollout=[mutate(checkout, _set_image("nginx:1.27-alpine-typo"))],
        expected_runbooks=["imagepullbackoff.md"],
    ))
    add(Case(
        "img_registry_first", "First deploy from a registry host that does not resolve", "image_pull", ["no_action"],
        "Image registry.example.invalid/ledger/api:2.4.1 on revision 1; the registry host does not exist.",
        "ledger", [deployment("ledger", [container("ledger", image="registry.example.invalid/ledger/api:2.4.1")])],
        waiting("ErrImagePull", "ImagePullBackOff"), expected_runbooks=["imagepullbackoff.md"],
    ))
    billing = _healthy("billing")
    add(Case(
        "img_private_rollout", "Rollout to an image in a repo that does not exist / needs auth", "image_pull",
        ["rollback_deployment"],
        "Deployment updated to ghcr.io/acme-payments/billing-api:3.1.0, a repository that does not exist publicly.",
        "billing", [billing], waiting("ErrImagePull", "ImagePullBackOff"),
        rollout=[mutate(billing, _set_image("ghcr.io/acme-payments/billing-api:3.1.0"))],
        expected_runbooks=["imagepullbackoff.md"], difficulty="medium",
    ))

    # --- app crash ---------------------------------------------------------
    api = _healthy("api")

    def bad_cmd(d: Obj) -> None:
        pod_spec(d)["containers"][0]["command"] = ["/app/server", "--port", "8080"]

    add(Case(
        "crash_bad_command_rollout", "Rollout with a command that does not exist in the image", "app_crash",
        ["rollback_deployment"],
        "Deployment updated to run /app/server, which does not exist in the image (exec fails).",
        "api", [api], restarts_at_least(2), rollout=[mutate(api, bad_cmd)],
        expected_runbooks=["create-container-error.md", "crashloopbackoff.md"],
    ))
    add(Case(
        "crash_traceback_first", "Worker crashes with an unhandled exception on start", "app_crash", ["no_action"],
        "Revision 1 of the worker raises KeyError on a job type and exits 1 (a code bug).",
        "jobs-worker",
        [deployment("jobs-worker", [container("jobs-worker", script=heredoc_then_exit([
            "INFO worker starting (queue=jobs, concurrency=4)",
            "INFO connected to queue jobs",
            "Traceback (most recent call last):",
            '  File "/app/worker.py", line 42, in <module>',
            "    handler = HANDLERS[job['type']]",
            "KeyError: 'invoice.v2'",
        ], 1, pre="sleep 2\n"))])],
        restarts_at_least(2), expected_runbooks=["crashloopbackoff.md"],
    ))
    gateway = _healthy("gateway")

    def panic(d: Obj) -> None:
        pod_spec(d)["containers"][0]["command"] = ["sh", "-c", heredoc_then_exit([
            "INFO starting gateway v2.0.0",
            "INFO loading 14 routes",
            "panic: runtime error: invalid memory address or nil pointer dereference",
            "[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x6b2f1a]",
            "",
            "goroutine 1 [running]:",
            "main.(*Router).Register(0x0, {0xc0001a2000, 0x9})",
            "\t/src/router.go:88 +0x3a",
            "main.main()",
            "\t/src/main.go:41 +0x1c5",
        ], 2)]

    add(Case(
        "crash_panic_rollout", "Rollout of a version that panics on start", "app_crash", ["rollback_deployment"],
        "Deployment updated to v2.0.0 whose process panics (nil pointer) and exits 2.",
        "gateway", [gateway], restarts_at_least(2), rollout=[mutate(gateway, panic)],
        expected_runbooks=["crashloopbackoff.md"],
    ))
    add(Case(
        "init_migration_first", "Init container (DB migration) fails", "app_crash", ["no_action"],
        "Revision 1 has an init container running a migration that fails ('relation already exists') and exits 1.",
        "orders",
        [deployment("orders", [container("orders", script=serve_loop("orders"))], init=[container(
            "run-migrations", script=heredoc_then_exit([
                "applying migration 0041_add_index ... ok",
                "applying migration 0042_orders_table ...",
                'ERROR: relation "orders" already exists (SQLSTATE 42P07)',
                "migration 0042 failed; aborting",
            ], 1))])],
        waiting("CrashLoopBackOff"), expected_runbooks=["crashloopbackoff.md"], difficulty="hard",
        notes="Diagnose fetches main-container logs only, so the migration error is not in the context.",
    ))

    # --- bad config value --------------------------------------------------
    port_script = (
        'echo "$(date -Iseconds) INFO payments starting"; '
        'case "$PORT" in ""|*[!0-9]*) echo "$(date -Iseconds) FATAL invalid value for PORT: \\"$PORT\\" '
        '(expected an integer 1-65535)"; exit 2;; esac; ' + serve_loop("payments")
    )
    payments = deployment("payments", [container("payments", script=port_script, env={"PORT": "8080"})])

    def bad_port(d: Obj) -> None:
        pod_spec(d)["containers"][0]["env"] = [{"name": "PORT", "value": "eighty"}]

    add(Case(
        "config_port_rollout", "Rollout sets PORT to a non-numeric value", "bad_config_value", ["rollback_deployment"],
        "Deployment updated with env PORT=eighty; the app validates PORT and exits 2.",
        "payments", [payments], restarts_at_least(2), rollout=[mutate(payments, bad_port)],
        expected_runbooks=["crashloopbackoff.md"],
    ))
    loglevel_script = (
        'case "$LOG_LEVEL" in debug|info|warn|error) ;; *) echo "FATAL unknown log level \\"$LOG_LEVEL\\" '
        '(valid: debug, info, warn, error)"; exit 1;; esac; ' + serve_loop("notifier")
    )
    add(Case(
        "config_loglevel_first", "ConfigMap value LOG_LEVEL=verbose is rejected", "bad_config_value", ["no_action"],
        "ConfigMap notifier-config sets LOG_LEVEL=verbose; the app only accepts debug/info/warn/error and exits 1.",
        "notifier",
        [configmap("notifier-config", {"LOG_LEVEL": "verbose", "SMTP_HOST": "smtp.internal"}),
         deployment("notifier", [container("notifier", script=loglevel_script,
                                           env_from=[{"configMapRef": {"name": "notifier-config"}}])])],
        restarts_at_least(2), expected_runbooks=["crashloopbackoff.md"],
    ))

    # --- missing config ----------------------------------------------------
    cart = _healthy("cart")

    def add_envfrom(d: Obj) -> None:
        pod_spec(d)["containers"][0]["envFrom"] = [{"configMapRef": {"name": "cart-feature-flags"}}]

    add(Case(
        "missing_cm_envfrom_rollout", "Rollout references a ConfigMap that was never created", "missing_config",
        ["rollback_deployment"],
        "Deployment updated with envFrom ConfigMap cart-feature-flags, which does not exist.",
        "cart", [cart], waiting("CreateContainerConfigError"), rollout=[mutate(cart, add_envfrom)],
        expected_runbooks=["create-container-config-error.md"],
    ))
    add(Case(
        "missing_secret_first", "Secret for DB password does not exist", "missing_config", ["no_action"],
        "Revision 1 reads DB_PASSWORD from Secret orders-db-credentials, which does not exist.",
        "reports",
        [deployment("reports", [container("reports", script=serve_loop("reports"), env_extra=[{
            "name": "DB_PASSWORD",
            "valueFrom": {"secretKeyRef": {"name": "orders-db-credentials", "key": "password"}}}])])],
        waiting("CreateContainerConfigError"), expected_runbooks=["create-container-config-error.md"],
    ))
    add(Case(
        "missing_cm_volume_first", "ConfigMap volume does not exist", "missing_config", ["no_action"],
        "Revision 1 mounts ConfigMap search-synonyms as a volume; it does not exist, so the pod is stuck creating.",
        "search-indexer",
        [deployment("search-indexer", [container("search-indexer", script=serve_loop("search-indexer"),
                                                 mounts=[{"name": "synonyms", "mountPath": "/etc/synonyms"}])],
                    volumes=[{"name": "synonyms", "configMap": {"name": "search-synonyms"}}])],
        waiting("ContainerCreating"), expected_runbooks=["failed-mount.md", "create-container-config-error.md"],
        settle_s=90, difficulty="medium",
    ))
    ledger_api = deployment("ledger-api", [container("ledger-api", script=serve_loop("ledger-api"), env_extra=[{
        "name": "API_TOKEN", "valueFrom": {"secretKeyRef": {"name": "ledger-api", "key": "api-token"}}}])])

    def wrong_key(d: Obj) -> None:
        pod_spec(d)["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["key"] = "api_token"

    add(Case(
        "missing_secret_key_rollout", "Rollout references a key that is not in the Secret", "missing_config",
        ["rollback_deployment"],
        "Secret ledger-api has key api-token; the new revision asks for api_token.",
        "ledger-api", [secret("ledger-api", {"api-token": "not-a-real-token"}), ledger_api],
        waiting("CreateContainerConfigError"), rollout=[mutate(ledger_api, wrong_key)],
        expected_runbooks=["create-container-config-error.md"], difficulty="medium",
    ))

    # --- OOM ---------------------------------------------------------------
    add(Case(
        "oom_first", "Catalog load exceeds a 48Mi limit", "oom_killed", ["no_action"],
        "Revision 1 loads ~300MB into memory with a 48Mi limit and is OOMKilled.",
        "catalog",
        [deployment("catalog", [container("catalog", script=(
            'echo "$(date -Iseconds) INFO loading product catalog into memory"; sleep 3; '
            "head -c 300m /dev/zero | tail; echo done"),
            resources={"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"memory": "48Mi"}})])],
        last_terminated("OOMKilled"), expected_runbooks=["oomkilled.md"],
    ))
    recs_script = (
        'echo "$(date -Iseconds) INFO recommender starting, warming model cache"; '
        "cache=$(head -c 80m /dev/zero | tr '\\0' a); "
        'echo "$(date -Iseconds) INFO cache warm (${#cache} bytes)"; ' + serve_loop("recommender")
    )
    recs = deployment("recommender", [container("recommender", script=recs_script, resources={
        "requests": {"cpu": "10m", "memory": "64Mi"}, "limits": {"memory": "512Mi"}})])

    def low_limit(d: Obj) -> None:
        pod_spec(d)["containers"][0]["resources"] = {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"memory": "64Mi"}}

    add(Case(
        "oom_limit_rollout", "Rollout lowers the memory limit below the working set", "oom_killed",
        ["rollback_deployment"],
        "Deployment updated from a 512Mi to a 64Mi memory limit; the app needs ~160MB to warm its cache.",
        "recommender", [recs], last_terminated("OOMKilled"), rollout=[mutate(recs, low_limit)],
        expected_runbooks=["oomkilled.md"], settle_s=90,
    ))
    leak_script = (
        'echo "$(date -Iseconds) INFO ingest worker started"; x=""; i=0; '
        "while true; do i=$((i+1)); x=\"$x$(head -c 1m /dev/zero | tr '\\0' a)\"; "
        'echo "$(date -Iseconds) INFO processed batch $i"; sleep 2; done'
    )
    add(Case(
        "oom_leak_first", "Worker leaks ~1MB per batch until OOMKilled", "oom_killed", ["no_action"],
        "Revision 1 appends 1MB per batch to an in-memory buffer that is never freed; 64Mi limit.",
        "ingest",
        [deployment("ingest", [container("ingest", script=leak_script)])],
        last_terminated("OOMKilled"), expected_runbooks=["oomkilled.md", "memory-leak.md"],
        difficulty="hard", settle_s=60, notes="Logs only say 'processed batch N'; the OOM is in status/events.",
    ))

    # --- probes ------------------------------------------------------------
    frontend = _nginx("frontend")

    def bad_liveness(d: Obj) -> None:
        pod_spec(d)["containers"][0]["livenessProbe"] = http_probe("/healthz", 80, delay=3, period=4, failures=2)

    add(Case(
        "liveness_path_rollout", "Rollout adds a liveness probe on a path that 404s", "probe_failure",
        ["rollback_deployment"],
        "Deployment updated with livenessProbe GET /healthz:80; nginx returns 404 so the kubelet keeps restarting it.",
        "frontend", [frontend], restarts_at_least(2), rollout=[mutate(frontend, bad_liveness)],
        expected_runbooks=["probe-failures.md"],
    ))
    add(Case(
        "readiness_port_first", "Readiness probe checks a port nothing listens on", "probe_failure", ["no_action"],
        "Revision 1 has a TCP readiness probe on 8081; nginx listens on 80, so the pod never becomes Ready.",
        "docs",
        [deployment("docs", [container("docs", image=NGINX, port=80, readiness={
            "tcpSocket": {"port": 8081}, "initialDelaySeconds": 2, "periodSeconds": 5})])],
        running_not_ready, expected_runbooks=["probe-failures.md"], settle_s=60,
    ))
    slow = (
        'echo "$(date -Iseconds) INFO warming caches, this takes ~40s"; sleep 40; '
        'echo "$(date -Iseconds) INFO ready"; exec nginx -g "daemon off;"'
    )
    add(Case(
        "liveness_slow_start_first", "Liveness probe kills a slow-starting app", "probe_failure", ["no_action"],
        "App needs ~40s to start listening; livenessProbe has initialDelaySeconds=5, period 5, failureThreshold 2.",
        "analytics",
        [deployment("analytics", [container("analytics", image=NGINX, script=slow, port=80,
                                            liveness=http_probe("/", 80, delay=5, period=5, failures=2))])],
        restarts_at_least(2), expected_runbooks=["probe-failures.md"], difficulty="hard",
        notes="Looks like a crash loop; the container never logs an error.",
    ))
    profile = _healthy("profile")

    def ready_exec(d: Obj) -> None:
        pod_spec(d)["containers"][0]["readinessProbe"] = {
            "exec": {"command": ["cat", "/tmp/ready"]}, "initialDelaySeconds": 2, "periodSeconds": 5}

    add(Case(
        "readiness_exec_rollout", "Rollout adds an exec readiness probe for a file never written", "probe_failure",
        ["rollback_deployment"],
        "Deployment updated with readinessProbe `cat /tmp/ready`; the app never creates the file.",
        "profile", [profile], running_not_ready, rollout=[mutate(profile, ready_exec)],
        expected_runbooks=["probe-failures.md"], settle_s=60, difficulty="medium",
    ))

    # --- CPU throttling ----------------------------------------------------
    cpu_script = (
        'echo "$(date -Iseconds) INFO thumbnailer started"; b=0; '
        "while true; do b=$((b+1)); s=$(date +%s); i=0; "
        "while [ $i -lt 30000 ]; do i=$((i+1)); done; e=$(date +%s); "
        'echo "$(date -Iseconds) INFO batch $b: 30000 items in $((e-s))s"; done'
    )
    add(Case(
        "cpu_throttle_first", "CPU-bound worker under a 20m CPU limit", "cpu_throttling",
        ["no_action", "scale_deployment"],
        "CPU-bound worker with limits.cpu=20m; it is throttled and each batch takes many seconds.",
        "thumbnailer",
        [deployment("thumbnailer", [container("thumbnailer", script=cpu_script, resources={
            "requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"cpu": "20m", "memory": "64Mi"}})])],
        ready, difficulty="hard", settle_s=150,
        notes="Nothing fails; only metrics and slow batch times show it. No runbook covers throttling.",
    ))

    # --- scheduling --------------------------------------------------------
    add(Case(
        "pending_cpu_first", "Requests 64 CPUs on a single small node", "unschedulable", ["no_action"],
        "Revision 1 requests cpu: 64; no node can fit it.",
        "trainer",
        [deployment("trainer", [container("trainer", script=serve_loop("trainer"), resources={
            "requests": {"cpu": "64", "memory": "64Mi"}, "limits": {"memory": "64Mi"}})])],
        unscheduled, expected_runbooks=["pending-unschedulable.md"], settle_s=45,
    ))
    inventory_sel = _healthy("stock")

    def selector(d: Obj) -> None:
        pod_spec(d)["nodeSelector"] = {"disktype": "ssd"}

    add(Case(
        "pending_nodeselector_rollout", "Rollout adds a nodeSelector no node matches", "unschedulable",
        ["rollback_deployment"],
        "Deployment updated with nodeSelector disktype=ssd; no node has that label.",
        "stock", [inventory_sel], unscheduled, rollout=[mutate(inventory_sel, selector)],
        expected_runbooks=["pending-unschedulable.md"], settle_s=45,
    ))
    anti = deployment("session-cache", [container("session-cache", script=serve_loop("session-cache"))], replicas=2,
                      affinity={"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                          "labelSelector": {"matchLabels": {"app": "session-cache"}},
                          "topologyKey": "kubernetes.io/hostname"}]}})
    add(Case(
        "pending_antiaffinity_first", "Required anti-affinity with 2 replicas on 1 node", "unschedulable",
        ["no_action", "scale_deployment"],
        "2 replicas with required pod anti-affinity on hostname, but the cluster has one node: one pod stays Pending.",
        "session-cache", [anti], unscheduled, expected_runbooks=["pending-unschedulable.md"],
        difficulty="hard", settle_s=45, notes="Scaling to 1 replica also clears the Pending pod.",
    ))

    # --- PVC ---------------------------------------------------------------
    add(Case(
        "pvc_storageclass_first", "PVC asks for a StorageClass that does not exist", "pvc_pending", ["no_action"],
        "PVC uploads-data uses storageClassName fast-ssd, which does not exist; the pod stays Pending.",
        "uploads",
        [{"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": "uploads-data"},
          "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "fast-ssd",
                   "resources": {"requests": {"storage": "1Gi"}}}},
         deployment("uploads", [container("uploads", script=serve_loop("uploads"),
                                          mounts=[{"name": "data", "mountPath": "/data"}])],
                    volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "uploads-data"}}])],
        pending, expected_runbooks=["pvc-pending.md"], settle_s=45,
    ))
    add(Case(
        "pvc_missing_first", "Pod mounts a PVC that was never created", "pvc_pending", ["no_action"],
        "Deployment mounts PVC exports-data, which does not exist.",
        "exports",
        [deployment("exports", [container("exports", script=serve_loop("exports"),
                                          mounts=[{"name": "data", "mountPath": "/data"}])],
                    volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "exports-data"}}])],
        pending, expected_runbooks=["pvc-pending.md"], settle_s=45, difficulty="medium",
    ))

    # --- dependency / network ----------------------------------------------
    client_script = (
        'echo "$(date -Iseconds) INFO storefront starting, upstream=$UPSTREAM"; '
        "while true; do "
        'curl -fsS -m 3 -o /dev/null "$UPSTREAM" || { echo "$(date -Iseconds) ERROR upstream request failed, exiting"; exit 1; }; '
        'echo "$(date -Iseconds) INFO upstream ok"; sleep 5; done'
    )
    add(Case(
        "svc_port_mismatch", "Client crashloops: upstream Service targetPort is wrong", "dependency_connectivity",
        ["no_action"],
        "Service inventory maps port 80 to targetPort 8080, but the inventory pods listen on 80; storefront exits on connection refused.",
        "storefront",
        [_nginx("inventory"), service("inventory", "inventory", 80, 8080),
         deployment("storefront", [container("storefront", image=CURL, script=client_script,
                                             env={"UPSTREAM": "http://inventory/stock"})])],
        restarts_at_least(2), difficulty="hard",
        notes="The Service spec is not in the context; only the client's curl error is.",
    ))
    add(Case(
        "dns_missing_service", "Client calls a Service name that does not exist", "dependency_connectivity",
        ["no_action"],
        "quotes calls http://pricing-api/quote; there is no pricing-api Service, so DNS lookup fails.",
        "quotes",
        [deployment("quotes", [container("quotes", image=CURL, script=client_script.replace("storefront", "quotes"),
                                         env={"UPSTREAM": "http://pricing-api/quote"})])],
        restarts_at_least(2), expected_runbooks=["dns-issues.md"], difficulty="medium",
    ))
    degraded = (
        'echo "$(date -Iseconds) INFO web starting, checking upstream $UPSTREAM"; '
        'if wget -q -T 2 -O /dev/null "$UPSTREAM"; then touch /tmp/ready; '
        'echo "$(date -Iseconds) INFO upstream ok"; else '
        'echo "$(date -Iseconds) WARN upstream $UPSTREAM unreachable at startup; continuing in degraded mode"; fi; '
        "while true; do sleep 5; done"
    )
    deps = _nginx("deps-api")
    deps["spec"]["replicas"] = 0
    add(Case(
        "stale_dependency", "App stuck in degraded mode after its dependency recovered", "dependency_connectivity",
        ["restart_deployment", "delete_pod"],
        "web started while its upstream deps-api had 0 replicas and only checks it at startup; deps-api is back "
        "now, but web stays not-Ready until it is restarted.",
        "web",
        [deps, service("deps-api", "deps-api", 80, 80),
         deployment("web", [container("web", script=degraded, env={"UPSTREAM": "http://deps-api/"},
                                      readiness={"exec": {"command": ["cat", "/tmp/ready"]},
                                                 "initialDelaySeconds": 2, "periodSeconds": 5})])],
        running_not_ready, hook="stale_dependency", difficulty="hard", settle_s=45,
        expected_runbooks=["probe-failures.md"],
        notes="A restart (new pod) fixes it. delete_pod is also a fix but policy only allows it for crashlooping pods.",
    ))

    # --- pod disruption ----------------------------------------------------
    add(Case(
        "pod_kill_single", "One replica killed by Chaos Mesh", "pod_disruption", ["no_action"],
        "Chaos Mesh PodChaos pod-kill on one of 2 replicas of search; the ReplicaSet replaced it.",
        "search", [_nginx("search", replicas=2)], ready, hook="pod_kill:1", settle_s=30,
        difficulty="medium", notes="Target is the healthy replacement pod; only lifecycle signals show the kill.",
    ))
    add(Case(
        "pod_kill_repeated", "Replicas killed 3 times in 90s", "pod_disruption", ["no_action"],
        "Chaos Mesh PodChaos pod-kill three times, 30s apart, on catalog-api (2 replicas).",
        "catalog-api", [_nginx("catalog-api", replicas=2)], ready, hook="pod_kill:3", settle_s=20,
        difficulty="medium",
    ))

    # --- a healthy previous revision exists, but rolling back would not help ----
    def bump_version(d: Obj) -> None:
        pod_spec(d)["containers"][0].setdefault("env", []).append({"name": "APP_VERSION", "value": "1.5.0"})

    pricing_script = (
        'case "$LOG_LEVEL" in debug|info|warn|error) ;; *) echo "FATAL unknown log level \\"$LOG_LEVEL\\" '
        '(valid: debug, info, warn, error)"; exit 1;; esac; ' + serve_loop("pricing")
    )
    pricing = deployment("pricing", [container("pricing", script=pricing_script,
                                               env_from=[{"configMapRef": {"name": "pricing-config"}}])])
    add(Case(
        "cm_deleted_after_rollout", "Shared ConfigMap deleted after a healthy rollout, then scaled up",
        "missing_config", ["no_action"],
        "Both revisions use ConfigMap pricing-config. After a healthy rollout someone deleted it; the new replica "
        "from a scale-up fails with CreateContainerConfigError. Rolling back would not help.",
        "pricing", [configmap("pricing-config", {"LOG_LEVEL": "info"}), pricing],
        waiting("CreateContainerConfigError"), rollout=[mutate(pricing, bump_version)],
        post=[["delete", "configmap", "pricing-config"], ["scale", "deploy/pricing", "--replicas=2"]],
        expected_runbooks=["create-container-config-error.md"], difficulty="hard",
        notes="Previous revision exists, so 'rollback whenever you can' is wrong here.",
    ))
    tax = deployment("tax", [container("tax", script=pricing_script.replace("pricing", "tax"),
                                       env_from=[{"configMapRef": {"name": "tax-config"}}])])
    add(Case(
        "cm_edited_after_rollout", "Shared ConfigMap edited to a bad value after a healthy rollout",
        "bad_config_value", ["no_action"],
        "Both revisions read tax-config. After a healthy rollout LOG_LEVEL was edited to 'verbose'; the new replica "
        "from a scale-up exits 1. Rolling back would not help; the ConfigMap must be fixed.",
        "tax", [configmap("tax-config", {"LOG_LEVEL": "info"}), tax],
        restarts_at_least(2), rollout=[mutate(tax, bump_version)],
        post=[["patch", "configmap", "tax-config", "-p", '{"data":{"LOG_LEVEL":"verbose"}}'],
              ["scale", "deploy/tax", "--replicas=2"]],
        expected_runbooks=["crashloopbackoff.md"], difficulty="hard",
    ))
    shipping = _nginx("shipping", replicas=2)
    add(Case(
        "pod_kill_after_rollout", "Replica killed some minutes after a healthy rollout", "pod_disruption",
        ["no_action"],
        "Healthy rollout of shipping (2 replicas), then Chaos Mesh killed one pod; the ReplicaSet replaced it.",
        "shipping", [shipping], ready, rollout=[mutate(shipping, bump_version)], hook="pod_kill:1", settle_s=30,
        difficulty="hard",
    ))
    accounts = _nginx("accounts")
    add(Case(
        "healthy_after_rollout", "Healthy pod right after a routine rollout", "healthy", ["no_action"],
        "No fault: a routine rollout (env change) completed and the new pod is healthy.",
        "accounts", [accounts], ready, rollout=[mutate(accounts, bump_version)], settle_s=60, difficulty="medium",
    ))

    # --- healthy -----------------------------------------------------------
    add(Case(
        "healthy_web", "Healthy nginx", "healthy", ["no_action"], "No fault injected.",
        "landing", [_nginx("landing")], ready, settle_s=60,
    ))
    noisy = (
        'echo "$(date -Iseconds) INFO sync worker started"; n=0; while true; do n=$((n+1)); '
        'echo "$(date -Iseconds) WARN upstream timeout on attempt 1/3, retrying"; '
        'echo "$(date -Iseconds) INFO sync $n ok after retry (records=120)"; '
        'echo "$(date -Iseconds) WARN cache hit ratio 0.41 below target 0.60"; sleep 4; done'
    )
    add(Case(
        "healthy_noisy_logs", "Healthy worker that logs routine warnings", "healthy", ["no_action"],
        "No fault injected; the worker logs benign retry/cache warnings.",
        "sync", [deployment("sync", [container("sync", script=noisy)])], ready, settle_s=60, difficulty="hard",
    ))
    add(Case(
        "healthy_job_completed", "Completed Job pod", "healthy", ["no_action"],
        "No fault; a one-off Job ran to completion (exit 0).",
        "nightly-report",
        [{"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "nightly-report"},
          "spec": {"backoffLimit": 0, "template": {"metadata": {"labels": {"app": "nightly-report"}}, "spec": {
              "restartPolicy": "Never", "containers": [container("report", script=(
                  'echo "INFO generating nightly report"; sleep 2; echo "INFO wrote 1342 rows to report-2026-11-13.csv"; '
                  'echo "INFO done"'))]}}}}],
        succeeded, settle_s=10, difficulty="medium",
    ))
    return cases


CASES = build_cases()
CASES_BY_ID = {c.id: c for c in CASES}
