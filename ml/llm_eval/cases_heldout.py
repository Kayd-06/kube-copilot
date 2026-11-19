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
    CURL,
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
    secret,
    serve_loop,
    service,
    unscheduled,
    waiting,
)

NODE = "kube-copilot-control-plane"


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
    cases.extend(build_traps())
    return cases


def _bump(var: str, value: str):
    """A routine rollout: one env value changes and nothing else."""
    def fn(d: Obj) -> None:
        for e in pod_spec(d)["containers"][0].setdefault("env", []):
            if e["name"] == var:
                e["value"] = value
                return
        pod_spec(d)["containers"][0]["env"].append({"name": var, "value": value})

    return fn


def build_traps() -> list[Case]:
    """Rollback traps: a healthy routine rollout happened shortly before the incident, but the cause is elsewhere.

    Added in PR #34 after the PR #33 report noted the held-out set had none. Labels and the hand-check
    rubric (ml/data/llm_eval/trap_rubric.md) were written and committed before any model saw these cases.
    In every one the previous revision would fail the same way, so `rollback_deployment` is wrong.
    """
    cases: list[Case] = []
    add = cases.append

    # 1. Dependency outage: the upstream Deployment is scaled to 0 after the client's routine rollout.
    checkout_script = (
        'echo "$(date -Iseconds) INFO checkout $APP_VERSION starting, ledger=$LEDGER_URL"; '
        "while true; do "
        'curl -fsS -m 3 -o /dev/null "$LEDGER_URL" || { echo "$(date -Iseconds) ERROR ledger request failed, exiting"; exit 1; }; '
        'echo "$(date -Iseconds) INFO ledger ok"; sleep 5; done'
    )
    ledger = deployment("ledger", [container("ledger", image=NGINX, port=80)])
    checkout = deployment("checkout", [container("checkout", image=CURL, script=checkout_script,
                                                 env={"APP_VERSION": "2.3.0", "LEDGER_URL": "http://ledger/"})])
    add(Case(
        "ho_trap_dependency_down", "Upstream scaled to zero shortly after a routine client rollout",
        "dependency_connectivity", ["no_action"],
        "checkout rolled out APP_VERSION 2.3.0 -> 2.3.1 (healthy). Then its upstream Deployment ledger was scaled "
        "to 0, so checkout's curl gets connection refused and it crashloops. Both revisions fail the same way; the "
        "upstream must be restored.",
        "checkout", [ledger, service("ledger", "ledger", 80, 80), checkout],
        restarts_at_least(2), rollout=[mutate(checkout, _bump("APP_VERSION", "2.3.1"))],
        post=[["scale", "deploy/ledger", "--replicas=0"]],
        expected_runbooks=["crashloopbackoff.md", "dns-issues.md"], difficulty="hard",
        notes="Rollback trap. The only template change is APP_VERSION.",
    ))

    # 2. A mounted ConfigMap is edited to an invalid value after a routine rollout; the app hot-reloads it.
    pricing_script = (
        'echo "$(date -Iseconds) INFO quote-engine build $BUILD starting"; '
        "while true; do "
        "v=$(sed -n 's/^max_discount_pct=//p' /etc/quote/engine.properties); "
        'case "$v" in [0-9]|[0-9][0-9]) ;; *) echo "$(date -Iseconds) FATAL /etc/quote/engine.properties: '
        'max_discount_pct=\"$v\" is not an integer 0-99"; exit 1;; esac; '
        'echo "$(date -Iseconds) INFO config ok (max_discount_pct=$v), serving quotes"; sleep 5; done'
    )
    quote = deployment("quote-engine", [container(
        "quote-engine", script=pricing_script, env={"BUILD": "881", "REGION": "eu-west-1"},
        mounts=[{"name": "conf", "mountPath": "/etc/quote", "readOnly": True}])],
        volumes=[{"name": "conf", "configMap": {"name": "quote-engine-conf"}}])
    add(Case(
        "ho_trap_configmap_reload", "Mounted ConfigMap edited to an invalid value after a routine rollout",
        "bad_config_value", ["no_action"],
        "quote-engine rolled out BUILD 881 -> 882 (healthy). Later the mounted ConfigMap quote-engine-conf was "
        "edited to max_discount_pct=thirty; the app re-reads the file, rejects it and exits 1 on every start. Both "
        "revisions mount the same ConfigMap; the ConfigMap must be fixed.",
        "quote-engine", [configmap("quote-engine-conf", {"engine.properties": "max_discount_pct=30\n"}), quote],
        restarts_at_least(2), rollout=[mutate(quote, _bump("BUILD", "882"))],
        post=[["patch", "configmap", "quote-engine-conf", "-p",
               '{"data":{"engine.properties":"max_discount_pct=thirty\\n"}}']],
        expected_runbooks=["crashloopbackoff.md"], difficulty="hard",
        notes="Rollback trap. The ConfigMap is not part of the pod template, so the diff shows only BUILD.",
    ))

    # 3. The only node is cordoned (maintenance) and the pod is deleted, after a routine rollout.
    reports = _svc("reports")
    pod_spec(reports)["containers"][0]["env"] = [{"name": "FEATURE_PDF_EXPORT", "value": "false"}]
    add(Case(
        "ho_trap_node_cordoned", "Node cordoned for maintenance after a routine rollout; replacement pod Pending",
        "unschedulable", ["no_action"],
        "reports rolled out FEATURE_PDF_EXPORT false -> true (healthy). Then the only node was cordoned and the "
        "pod deleted (as a drain would); the replacement stays Pending because the node is unschedulable. A "
        "rolled-back pod could not schedule either; the node must be uncordoned or capacity added.",
        "reports", [reports], unscheduled, rollout=[mutate(reports, _bump("FEATURE_PDF_EXPORT", "true"))],
        post=[["cordon", NODE], ["delete", "pod", "-l", "app=reports", "--wait=false"]],
        cleanup=[["uncordon", NODE]],
        expected_runbooks=["pending-unschedulable.md", "node-not-ready.md"], settle_s=40, difficulty="medium",
        notes="Rollback trap. Cordons the cluster's only node: capture alone (--parallel 1).",
    ))

    # 4. A Secret both revisions read is deleted after a routine rollout; the replacement pod cannot start.
    notifier = deployment("notifier", [container(
        "notifier", script=serve_loop("notifier"), env={"SMTP_HOST": "smtp.internal", "TEMPLATE_SET": "v7"},
        env_extra=[{"name": "SMTP_PASSWORD",
                    "valueFrom": {"secretKeyRef": {"name": "smtp-credentials", "key": "password"}}}])])
    add(Case(
        "ho_trap_secret_deleted", "Secret deleted after a routine rollout; the replacement pod cannot start",
        "missing_config", ["no_action"],
        "notifier rolled out TEMPLATE_SET v7 -> v8 (healthy). Then Secret smtp-credentials was deleted and the pod "
        "was deleted; the replacement fails with CreateContainerConfigError. The previous revision reads the same "
        "Secret; it must be restored.",
        "notifier", [secret("smtp-credentials", {"password": "not-a-real-password"}), notifier],
        waiting("CreateContainerConfigError"), rollout=[mutate(notifier, _bump("TEMPLATE_SET", "v8"))],
        post=[["delete", "secret", "smtp-credentials"], ["delete", "pod", "-l", "app=notifier", "--wait=false"]],
        expected_runbooks=["create-container-config-error.md"], difficulty="medium",
        notes="Rollback trap.",
    ))

    # 5. Healthy after a routine rollout, but the logs are full of retried timeouts that succeed.
    retry_script = (
        'echo "$(date -Iseconds) INFO feed-sync $RELEASE started"; n=0; while true; do n=$((n+1)); '
        'echo "$(date -Iseconds) WARN partner-api timeout after 2000ms (attempt 1/3), retrying"; '
        'echo "$(date -Iseconds) WARN partner-api 503 Service Unavailable (attempt 2/3), retrying"; '
        'echo "$(date -Iseconds) INFO partner-api ok on attempt 3, synced page $n (200 items)"; sleep 4; done'
    )
    feed = deployment("feed-sync", [container("feed-sync", script=retry_script, env={"RELEASE": "r41"})])
    add(Case(
        "ho_trap_healthy_retry_logs", "Healthy after a routine rollout; logs show retried timeouts that succeed",
        "healthy", ["no_action"],
        "feed-sync rolled out RELEASE r41 -> r42. Nothing is broken: the pod is Ready with no restarts, and the "
        "WARN lines are retries that succeed on the third attempt (written by the workload script).",
        "feed-sync", [feed], ready, rollout=[mutate(feed, _bump("RELEASE", "r42"))], settle_s=75,
        difficulty="hard", notes="Rollback trap and a noisy-logs check.",
    ))
    return cases


HELDOUT_CASES = build_heldout()
HELDOUT_BY_ID = {c.id: c for c in HELDOUT_CASES}
