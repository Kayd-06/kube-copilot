"""Held-out incidents for the LLM eval, written after the PR #31 failures were known.

The prompt and context changes in this PR were made after reading the
failures on the 38 cases in cases.py, so scores on that set are optimistic.
These 12 cases were written, labelled from the injected fault and captured
*after* those changes were finished, and were never used to tune anything.
They reuse the same fault families with different workloads, values and
mechanics, so they test whether the fixes generalise rather than whether they
memorised the original cases.

Captured with `python -m ml.llm_eval.capture --set heldout` into
ml/data/llm_eval/heldout/ (namespaces kc-ho-NN).
"""

from __future__ import annotations

from ml.llm_eval.cases import (
    BUSYBOX,
    NGINX,
    Case,
    Obj,
    configmap,
    container,
    deployment,
    http_probe,
    last_terminated,
    mutate,
    pod_spec,
    ready,
    restarts_at_least,
    running_not_ready,
    serve_loop,
    unscheduled,
    waiting,
)


def _svc(name: str, **kw) -> Obj:
    return deployment(name, [container(name, script=serve_loop(name), **kw)])


def build_heldout() -> list[Case]:
    cases: list[Case] = []
    add = cases.append

    invoice = _svc("invoice")
    add(Case(
        "ho_img_quay_rollout", "Rollout to an image on a registry path that does not exist", "image_pull",
        ["rollback_deployment"],
        "Deployment updated from busybox to quay.io/acme-billing/invoice:7.2.0, which does not exist.",
        "invoice", [invoice], waiting("ErrImagePull", "ImagePullBackOff"),
        rollout=[mutate(invoice, lambda d: pod_spec(d)["containers"][0].update(image="quay.io/acme-billing/invoice:7.2.0"))],
        expected_runbooks=["imagepullbackoff.md"],
    ))

    router = _svc("router")

    def unknown_flag(d: Obj) -> None:
        pod_spec(d)["containers"][0]["command"] = ["sh", "-c", (
            'echo "router v3.0.0"; echo "error: unknown flag: --worker-threads" >&2; '
            'echo "usage: router [--port N] [--log-format json|text]" >&2; exit 64')]

    add(Case(
        "ho_crash_flag_rollout", "Rollout passes a flag the new binary rejects", "app_crash", ["rollback_deployment"],
        "Deployment updated to a command that exits 64 with 'unknown flag: --worker-threads'.",
        "router", [router], restarts_at_least(2), rollout=[mutate(router, unknown_flag)],
        expected_runbooks=["crashloopbackoff.md"],
    ))

    db_script = (
        'echo "$(date -Iseconds) INFO accounts-api starting"; '
        'case "$DATABASE_URL" in postgres://*) ;; *) echo "$(date -Iseconds) FATAL invalid DATABASE_URL '
        '\\"$DATABASE_URL\\": expected postgres://user@host:port/db"; exit 1;; esac; ' + serve_loop("accounts-api")
    )
    accounts = deployment("accounts-api", [container("accounts-api", script=db_script,
                                                     env={"DATABASE_URL": "postgres://app@db:5432/accounts"})])

    def bad_url(d: Obj) -> None:
        pod_spec(d)["containers"][0]["env"] = [{"name": "DATABASE_URL", "value": "postgres//app@db:5432/accounts"}]

    add(Case(
        "ho_env_dburl_rollout", "Rollout sets a malformed DATABASE_URL", "bad_config_value", ["rollback_deployment"],
        "Deployment updated with DATABASE_URL missing the ':' after the scheme; the app validates it and exits 1.",
        "accounts-api", [accounts], restarts_at_least(2), rollout=[mutate(accounts, bad_url)],
        expected_runbooks=["crashloopbackoff.md"],
    ))

    help_center = deployment("help-center", [container("help-center", image=NGINX, port=80)])

    def ready_404(d: Obj) -> None:
        pod_spec(d)["containers"][0]["readinessProbe"] = http_probe("/ready", 80, delay=2, period=4, failures=2)

    add(Case(
        "ho_readiness_http_rollout", "Rollout adds an HTTP readiness probe on a path that 404s", "probe_failure",
        ["rollback_deployment"],
        "Deployment updated with readinessProbe GET /ready:80; nginx returns 404, so the new pod never becomes Ready.",
        "help-center", [help_center], running_not_ready, rollout=[mutate(help_center, ready_404)],
        expected_runbooks=["probe-failures.md"], settle_s=45, difficulty="medium",
    ))

    indexer = _svc("indexer")

    def huge_request(d: Obj) -> None:
        pod_spec(d)["containers"][0]["resources"] = {"requests": {"cpu": "10m", "memory": "512Gi"},
                                                     "limits": {"memory": "512Gi"}}

    add(Case(
        "ho_mem_request_rollout", "Rollout requests 512Gi of memory", "unschedulable", ["rollback_deployment"],
        "Deployment updated to request 512Gi memory; no node fits it, so the new pod stays Pending.",
        "indexer", [indexer], unscheduled, rollout=[mutate(indexer, huge_request)],
        expected_runbooks=["pending-unschedulable.md"], settle_s=45, difficulty="medium",
    ))

    encoder_script = (
        'echo "$(date -Iseconds) INFO encoder started"; n=0; '
        "while true; do n=$((n+1)); s=$(date +%s); i=0; "
        "while [ $i -lt 20000 ]; do i=$((i+1)); done; "
        'echo "$(date -Iseconds) INFO segment $n encoded in $(( $(date +%s) - s ))s"; done'
    )
    encoder = deployment("encoder", [container("encoder", script=encoder_script, resources={
        "requests": {"cpu": "50m", "memory": "16Mi"}, "limits": {"cpu": "250m", "memory": "64Mi"}})])

    def low_cpu(d: Obj) -> None:
        pod_spec(d)["containers"][0]["resources"] = {"requests": {"cpu": "10m", "memory": "16Mi"},
                                                     "limits": {"cpu": "15m", "memory": "64Mi"}}

    add(Case(
        "ho_cpu_limit_rollout", "Rollout cuts the CPU limit of a CPU-bound worker to 15m", "cpu_throttling",
        ["rollback_deployment"],
        "Deployment updated from a 250m to a 15m CPU limit; the CPU-bound encoder is throttled and slows down.",
        "encoder", [encoder], ready, rollout=[mutate(encoder, low_cpu)], settle_s=150, difficulty="hard",
        notes="Nothing fails; only throttling metrics, the limit and slower segments show it.",
    ))

    add(Case(
        "ho_oom_render_first", "Renderer allocates ~120MB under a 48Mi limit", "oom_killed", ["no_action"],
        "Revision 1 builds a ~120MB string in memory with a 48Mi limit and is OOMKilled.",
        "pdf-render",
        [deployment("pdf-render", [container("pdf-render", script=(
            'echo "$(date -Iseconds) INFO rendering monthly statements"; sleep 2; '
            "buf=$(head -c 120m /dev/zero | tr '\\0' a); echo \"rendered ${#buf}\""),
            resources={"requests": {"cpu": "10m", "memory": "24Mi"}, "limits": {"memory": "48Mi"}})])],
        last_terminated("OOMKilled"), expected_runbooks=["oomkilled.md"],
    ))

    add(Case(
        "ho_pod_kill_two", "Two of three replicas killed 30s apart", "pod_disruption", ["no_action"],
        "Chaos Mesh PodChaos pod-kill twice on media (3 replicas); the ReplicaSet replaced both pods.",
        "media",
        [deployment("media", [container("media", image=NGINX, port=80)], replicas=3)],
        ready, hook="pod_kill:2", settle_s=25, difficulty="medium",
    ))

    mailer_script = (
        'echo "$(date -Iseconds) INFO mailer started (batch=50)"; n=0; while true; do n=$((n+1)); '
        'echo "$(date -Iseconds) INFO sent batch $n (50 messages, 0 bounced)"; '
        'echo "$(date -Iseconds) DEBUG queue depth 3, retry budget ok"; sleep 5; done'
    )
    add(Case(
        "ho_healthy_mailer", "Healthy mailer with a generous CPU limit", "healthy", ["no_action"],
        "No fault injected; a quiet worker with a 200m CPU limit it barely uses.",
        "mailer",
        [deployment("mailer", [container("mailer", script=mailer_script, resources={
            "requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"cpu": "200m", "memory": "64Mi"}})])],
        ready, settle_s=75, difficulty="medium",
    ))

    auth = _svc("auth")

    def tls_volume(d: Obj) -> None:
        spec = pod_spec(d)
        spec["volumes"] = [{"name": "tls", "secret": {"secretName": "auth-tls"}}]
        spec["containers"][0]["volumeMounts"] = [{"name": "tls", "mountPath": "/etc/tls", "readOnly": True}]

    add(Case(
        "ho_secret_volume_rollout", "Rollout mounts a Secret that does not exist", "missing_config",
        ["rollback_deployment"],
        "Deployment updated to mount Secret auth-tls as a volume; the Secret does not exist, so the pod is stuck creating.",
        "auth", [auth], waiting("ContainerCreating"), rollout=[mutate(auth, tls_volume)],
        expected_runbooks=["failed-mount.md", "create-container-config-error.md"], settle_s=90, difficulty="medium",
    ))

    add(Case(
        "ho_cm_key_first", "Env var reads a key the ConfigMap does not have", "missing_config", ["no_action"],
        "Revision 1 reads RATE_LIMIT from ConfigMap gateway-limits, which exists but has no RATE_LIMIT key.",
        "edge",
        [configmap("gateway-limits", {"BURST": "20"}),
         deployment("edge", [container("edge", script=serve_loop("edge"), env_extra=[{
             "name": "RATE_LIMIT", "valueFrom": {"configMapKeyRef": {"name": "gateway-limits", "key": "RATE_LIMIT"}}}])])],
        waiting("CreateContainerConfigError"), expected_runbooks=["create-container-config-error.md"],
    ))

    dns_script = (
        'echo "$(date -Iseconds) INFO settlement worker starting, db proxy=$DB_PROXY"; '
        'wget -q -T 3 -O /dev/null "$DB_PROXY" || { echo "$(date -Iseconds) ERROR cannot reach db proxy, exiting"; exit 1; }; '
        "sleep 3600"
    )
    add(Case(
        "ho_dns_proxy_first", "Worker calls a Service name that does not exist", "dependency_connectivity",
        ["no_action"],
        "settlement calls http://ledger-db-proxy:6432/; no such Service exists, so the DNS lookup fails and it exits 1.",
        "settlement",
        [deployment("settlement", [container("settlement", image=BUSYBOX, script=dns_script,
                                             env={"DB_PROXY": "http://ledger-db-proxy:6432/"})])],
        restarts_at_least(2), expected_runbooks=["dns-issues.md"], difficulty="medium",
    ))
    return cases


HELDOUT_CASES = build_heldout()
HELDOUT_BY_ID = {c.id: c for c in HELDOUT_CASES}
