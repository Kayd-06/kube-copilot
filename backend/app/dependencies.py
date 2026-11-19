"""Which in-cluster Services a pod's env points at, and whether they are serving right now.

A log line like "upstream unreachable" says what the app saw when it ran the
check. This says what the dependency looks like *now*. Comparing the two is
how you tell a dependency that is still down from an app that is stuck on
state from startup, after the dependency already recovered.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models import DependencyStatus

logger = logging.getLogger(__name__)

MAX_DEPENDENCIES = 5
# scheme://[user@]host[:port][/...]   or   host:port[/...]
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/\s]*@)?(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*)(?::(?P<port>\d+))?(?:[/?#].*)?$")
_HOST_PORT = re.compile(r"^(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*):(?P<port>\d{2,5})(?:/.*)?$")
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def service_ref(value: str, pod_namespace: str) -> tuple[str, str] | None:
    """(namespace, service) when `value` is a URL or host:port naming an in-cluster Service, else None."""
    value = (value or "").strip()
    m = _URL.match(value) or _HOST_PORT.match(value)
    if not m:
        return None
    host = m.group("host").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    parts = host.split(".")
    if len(parts) == 1:
        name, ns = parts[0], pod_namespace
    elif len(parts) in (3, 5) and parts[2] == "svc" and parts[3:] in ([], ["cluster", "local"]):
        name, ns = parts[0], parts[1]  # name.namespace.svc[.cluster.local]
    else:
        # External names, and the ambiguous two-label "name.namespace" form, are not checked.
        return None
    if name in {"localhost"} or not _DNS_LABEL.match(name) or not _DNS_LABEL.match(ns):
        return None
    return ns, name


def referenced_services(pod: Any) -> list[tuple[str, str, str, str]]:
    """(source, value, namespace, service) for literal env values that point at a Service."""
    namespace = pod.metadata.namespace if pod.metadata else "default"
    out, seen = [], set()
    for c in (pod.spec.containers or []) if pod.spec else []:
        for env in c.env or []:
            if not env.value:
                continue
            ref = service_ref(env.value, namespace)
            if ref is None or ref in seen:
                continue
            seen.add(ref)
            out.append((f"env {env.name}", env.value, ref[0], ref[1]))
    return out[:MAX_DEPENDENCIES]


def _ready_since(pod: Any) -> datetime | None:
    for cond in (pod.status.conditions or []) if pod.status else []:
        if cond.type == "Ready" and cond.status == "True":
            ts = cond.last_transition_time
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
    return None


def _port_mismatch(svc: Any, pods: list[Any]) -> str | None:
    """Describe Service ports whose targetPort no backing pod declares (only when the pods declare ports)."""
    declared_nums, declared_names = set(), set()
    for p in pods:
        for c in (p.spec.containers or []) if p.spec else []:
            for port in c.ports or []:
                declared_nums.add(port.container_port)
                if port.name:
                    declared_names.add(port.name)
    if not declared_nums:
        return None
    bad = []
    for sp in (svc.spec.ports or []) if svc.spec else []:
        target = sp.target_port if sp.target_port is not None else sp.port
        if isinstance(target, str) and not target.isdigit():
            ok = target in declared_names
        else:
            ok = int(target) in declared_nums
        if not ok:
            bad.append(f"port {sp.port} -> targetPort {target}")
    if not bad:
        return None
    return f"Service {', '.join(bad)}, but its pods declare containerPort(s) {sorted(declared_nums)}"


def check_dependencies(core: Any, pod: Any) -> list[DependencyStatus]:
    """Look up each referenced Service and count its Ready backing pods. Never raises."""
    out: list[DependencyStatus] = []
    for source, value, ns, name in referenced_services(pod):
        status = DependencyStatus(source=source, target=value, namespace=ns, service=name, exists=False)
        try:
            svc = core.read_namespaced_service(name=name, namespace=ns)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                status.note = f"no Service named {name} in namespace {ns}; the name will not resolve"
                out.append(status)
            else:
                logger.info("Dependency check failed for %s/%s: %s", ns, name, exc)
            continue
        status.exists = True
        selector = (svc.spec.selector or {}) if svc.spec else {}
        if not selector:
            status.note = "Service has no selector (endpoints managed elsewhere); not checked"
            out.append(status)
            continue
        try:
            pods = core.list_namespaced_pod(
                namespace=ns, label_selector=",".join(f"{k}={v}" for k, v in sorted(selector.items()))
            ).items or []
        except Exception as exc:  # noqa: BLE001
            logger.info("Dependency pods unavailable for %s/%s: %s", ns, name, exc)
            out.append(status)
            continue
        pods = [p for p in pods if not (p.metadata and p.metadata.deletion_timestamp)]
        ready = [t for t in (_ready_since(p) for p in pods) if t is not None]
        ready_count = sum(1 for p in pods if _ready_since(p) is not None)
        status.port_mismatch = _port_mismatch(svc, pods)
        status.backing_pods = len(pods)
        status.ready_pods = ready_count
        status.ready_since = max(ready) if ready else None
        out.append(status)
    return out


def _fmt(ts: datetime | None) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else "?"


def dependency_lines(
    deps: list[DependencyStatus] | None, pod_started: datetime | None, restarts: int = 0
) -> list[str]:
    """Prompt lines; one per dependency with a plain verdict (missing / down / serving / recovered)."""
    if not deps:
        return []
    lines = []
    for d in deps:
        head = f"- {d.source}={d.target} -> Service {d.namespace}/{d.service}: "
        if not d.exists:
            lines.append(head + (d.note or "does not exist") + " -> DEPENDENCY MISSING")
            continue
        if d.ready_pods is None:
            lines.append(head + (d.note or "exists; backing pods unknown"))
            continue
        if d.ready_pods == 0:
            lines.append(head + f"0/{d.backing_pods} backing pods Ready -> DEPENDENCY DOWN NOW")
            continue
        since = d.ready_since.astimezone(timezone.utc) if d.ready_since and d.ready_since.tzinfo else d.ready_since
        started = pod_started.astimezone(timezone.utc) if pod_started and pod_started.tzinfo else pod_started
        text = f"{d.ready_pods}/{d.backing_pods} backing pods Ready, serving now"
        if since:
            text += f" (Ready since {_fmt(since)}"
            text += (", AFTER this pod was created" if started and since > started else
                     ", before this pod was created" if started else "") + ")"
        if d.port_mismatch:
            text += f"; PORT MISMATCH: {d.port_mismatch}, so connections to the Service fail"
        elif restarts:
            text += (f"; this pod's container has restarted {restarts} time(s) and keeps failing while the "
                     "dependency's pods are Ready, so the problem is between them (Service ports, network policy, "
                     "URL) or in this app, not stale startup state")
        elif since and started and since > started:
            text += ("; this pod has not restarted since it was created -> DEPENDENCY RECOVERED AFTER THIS POD "
                     "STARTED (anything the app decided at startup about it is stale)")
        lines.append(head + text)
    return ["Dependencies referenced in the pod's env (checked now, when this evidence was collected):", *lines]
