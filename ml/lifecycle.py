"""Pod-disruption detection from Kubernetes events and pod/workload lifecycle.

The Isolation Forest in ml/train.py scores a window of one pod's metrics. A
killed pod simply stops reporting, so on real data it caught about 4% of
`pod_kill` windows. This detector looks at what the cluster says happened
instead:

- `pod_replaced`         a workload got a new pod while nobody scaled it up
- `pod_vanished`         a pod's series ended while its workload still wanted it
- `replicas_unavailable` a Deployment had fewer available replicas than desired
- `container_restarted`  a container's restart count went up (with its reason)
- `event:<Reason>`       a disruption event on a Pod/ReplicaSet (Killing,
                         BackOff, OOMKilling, Evicted, NodeNotReady, ...)

Signals are attached to a *workload* (Deployment / StatefulSet / DaemonSet
name, derived from the owner or the pod name) and, when known, to a pod.
Scaling and rollouts are expected lifecycle churn, so replacements, deletions
and Killing events within `scale_grace_s` of a `ScalingReplicaSet` event, a
desired-replica change or a generation bump are not signals.

The module is pure: the offline evaluation (ml/eval_lifecycle.py) feeds it
recorded events and kube-state-metrics series, and the live API
(backend/app/anomaly/lifecycle.py) feeds it the Kubernetes API, so both run
exactly the same rules. Events about Chaos Mesh objects are never used.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

# Pod / ReplicaSet event reasons that mean "something disrupted this workload".
# Killing is only a disruption when no scale-down or rollout explains it.
DISRUPTION_EVENT_REASONS = frozenset(
    {
        "Killing",
        "BackOff",
        "OOMKilling",
        "Evicted",
        "Preempting",
        "NodeNotReady",
        "FailedKillPod",
        "ExceededGracePeriod",
        "Failed",
        "FailedScheduling",
        "FailedCreate",
        "TaintManagerEviction",
        "SuccessfulDelete",  # ReplicaSet deleted a pod
        "SuccessfulCreate",  # ReplicaSet replaced a pod
    }
)
# Reasons that are routine during a scale or rollout.
SCALING_EXPLAINS = frozenset({"Killing", "SuccessfulDelete", "SuccessfulCreate"})
SCALING_EVENT_REASONS = frozenset({"ScalingReplicaSet"})
# Signals that can be explained away by scaling.
_SCALABLE_KINDS = frozenset({"pod_replaced", "pod_vanished", "replicas_unavailable"})

_RS_HASH = re.compile(r"^(?P<base>.+)-[a-z0-9]{6,10}$")
_POD_SUFFIX = re.compile(r"^(?P<base>.+)-[a-z0-9]{5}$")


def workload_of(pod: str, owner_name: str | None = None, owner_kind: str | None = None) -> str:
    """Best-effort workload name for a pod.

    Deployment pods are `<deploy>-<rs hash>-<5 chars>` and owned by the
    ReplicaSet `<deploy>-<rs hash>`; DaemonSet pods are `<ds>-<5 chars>`;
    StatefulSet pods `<sts>-<ordinal>`.
    """
    if owner_name:
        if owner_kind in (None, "", "ReplicaSet"):
            match = _RS_HASH.match(owner_name)
            return match.group("base") if match else owner_name
        return owner_name
    match = _POD_SUFFIX.match(pod)
    base = match.group("base") if match else pod
    rs = _RS_HASH.match(base)
    if match and rs:
        return rs.group("base")
    if match:
        return base
    sts = re.match(r"^(?P<base>.+)-\d+$", pod)
    return sts.group("base") if sts else pod


def workload_of_replicaset(name: str) -> str:
    match = _RS_HASH.match(name)
    return match.group("base") if match else name


@dataclass(frozen=True)
class LifecycleEvent:
    ts: float  # last occurrence, unix seconds
    namespace: str
    kind: str  # involvedObject.kind
    name: str  # involvedObject.name
    reason: str
    type: str = "Normal"
    message: str = ""
    first_ts: float | None = None
    count: int = 1


@dataclass(frozen=True)
class PodLife:
    """One pod as seen over the observation period."""

    namespace: str
    pod: str
    workload: str
    created_ts: float
    first_seen: float
    last_seen: float
    # Live API only: when the owning ReplicaSet was created. A pod much younger
    # than its ReplicaSet is a replacement even if no older sibling is visible.
    owner_created_ts: float | None = None


@dataclass(frozen=True)
class WorkloadSample:
    ts: float
    namespace: str
    workload: str
    desired: float
    available: float
    generation: float = 0.0


@dataclass(frozen=True)
class RestartObservation:
    ts: float  # when the restart count was first seen higher (or lastState finishedAt)
    namespace: str
    pod: str
    workload: str
    delta: float = 1.0
    reason: str = ""


@dataclass(frozen=True)
class Signal:
    ts: float
    namespace: str
    workload: str
    pod: str | None
    kind: str
    detail: str = ""


@dataclass
class DetectorConfig:
    # A scale or rollout explains churn this long after (and slightly before) it.
    scale_grace_s: float = 120.0
    # A pod that already existed when observation began is not a replacement.
    observation_start: float | None = None
    observation_end: float | None = None
    # A series that stops within this long of the end of observation is just the end.
    vanish_tail_s: float = 60.0
    # Replica dips must last at least this long (one 15s scrape by default).
    min_unavailable_s: float = 0.0
    # Matching slack between a signal and a scoring window (scrape step).
    match_slack_s: float = 15.0
    event_reasons: frozenset[str] = field(default_factory=lambda: DISRUPTION_EVENT_REASONS)


def _event_workload(event: LifecycleEvent, pod_workloads: dict[tuple[str, str], str]) -> tuple[str, str | None]:
    if event.kind == "Pod":
        return pod_workloads.get((event.namespace, event.name)) or workload_of(event.name), event.name
    if event.kind == "ReplicaSet":
        return workload_of_replicaset(event.name), None
    return event.name, None


def scaling_times(
    events: Iterable[LifecycleEvent], samples: Iterable[WorkloadSample]
) -> dict[tuple[str, str], list[float]]:
    """Times when each workload was deliberately scaled or rolled out."""
    out: dict[tuple[str, str], list[float]] = defaultdict(list)
    for event in events:
        if event.reason in SCALING_EVENT_REASONS and event.kind == "Deployment":
            out[(event.namespace, event.name)].append(event.ts)
            if event.first_ts is not None and event.first_ts != event.ts:
                out[(event.namespace, event.name)].append(event.first_ts)
    by_workload: dict[tuple[str, str], list[WorkloadSample]] = defaultdict(list)
    for sample in samples:
        by_workload[(sample.namespace, sample.workload)].append(sample)
    for key, series in by_workload.items():
        series.sort(key=lambda s: s.ts)
        for prev, cur in zip(series, series[1:]):
            if cur.desired != prev.desired or (cur.generation and cur.generation != prev.generation):
                out[key].append(cur.ts)
    for times in out.values():
        times.sort()
    return out


def _near(ts: float, times: list[float], before: float, after: float) -> bool:
    return any(t - before <= ts <= t + after for t in times)


def detect(
    events: Iterable[LifecycleEvent] = (),
    pods: Iterable[PodLife] = (),
    workloads: Iterable[WorkloadSample] = (),
    restarts: Iterable[RestartObservation] = (),
    config: DetectorConfig | None = None,
) -> list[Signal]:
    """Return every disruption signal, sorted by time."""
    cfg = config or DetectorConfig()
    events = list(events)
    pods = list(pods)
    workloads = list(workloads)
    scaled = scaling_times(events, workloads)
    pod_workloads = {(p.namespace, p.pod): p.workload for p in pods}

    def explained(namespace: str, workload: str, ts: float) -> bool:
        # Slightly before too: kube-state-metrics can see the new pod a scrape
        # before the ScalingReplicaSet event's timestamp second.
        return _near(ts, scaled.get((namespace, workload), []), 30.0, cfg.scale_grace_s)

    signals: list[Signal] = []

    # Replacements and disappearances, from pod creation times.
    by_workload: dict[tuple[str, str], list[PodLife]] = defaultdict(list)
    for pod in pods:
        by_workload[(pod.namespace, pod.workload)].append(pod)
    for (ns, wl), members in by_workload.items():
        for pod in members:
            started_inside = cfg.observation_start is None or pod.created_ts > cfg.observation_start
            had_sibling_before = any(o.pod != pod.pod and o.created_ts < pod.created_ts for o in members)
            younger_than_owner = (
                pod.owner_created_ts is not None and pod.created_ts - pod.owner_created_ts > cfg.scale_grace_s
            )
            replaced = had_sibling_before or younger_than_owner
            if started_inside and replaced and not explained(ns, wl, pod.created_ts):
                signals.append(Signal(pod.created_ts, ns, wl, pod.pod, "pod_replaced", "new pod, no scale-up"))
            ended_early = cfg.observation_end is not None and pod.last_seen < cfg.observation_end - cfg.vanish_tail_s
            if ended_early and not explained(ns, wl, pod.last_seen):
                signals.append(Signal(pod.last_seen, ns, wl, pod.pod, "pod_vanished", "pod went away, no scale-down"))

    # Replica dips.
    series_by_wl: dict[tuple[str, str], list[WorkloadSample]] = defaultdict(list)
    for sample in workloads:
        series_by_wl[(sample.namespace, sample.workload)].append(sample)
    for (ns, wl), series in series_by_wl.items():
        series.sort(key=lambda s: s.ts)
        dip_start: float | None = None
        for sample in series:
            short = sample.desired > 0 and sample.available < sample.desired
            if short:
                dip_start = sample.ts if dip_start is None else dip_start
                if sample.ts - dip_start >= cfg.min_unavailable_s and not explained(ns, wl, sample.ts):
                    signals.append(
                        Signal(
                            sample.ts, ns, wl, None, "replicas_unavailable",
                            f"{sample.available:g}/{sample.desired:g} available",
                        )
                    )
            else:
                dip_start = None

    # Events.
    for event in events:
        if event.reason not in cfg.event_reasons or event.kind not in ("Pod", "ReplicaSet"):
            continue
        wl, pod = _event_workload(event, pod_workloads)
        if event.reason in SCALING_EXPLAINS and explained(event.namespace, wl, event.ts):
            continue
        detail = event.message[:120]
        signals.append(Signal(event.ts, event.namespace, wl, pod, f"event:{event.reason}", detail))

    # Container restarts.
    for obs in restarts:
        reason = f" ({obs.reason})" if obs.reason else ""
        signals.append(
            Signal(obs.ts, obs.namespace, obs.workload, obs.pod, "container_restarted", f"+{obs.delta:g}{reason}")
        )

    signals.sort(key=lambda s: (s.ts, s.namespace, s.workload, s.kind))
    return signals


class SignalIndex:
    """Look up signals for a (namespace, pod) window quickly."""

    def __init__(self, signals: Iterable[Signal], pod_workloads: dict[tuple[str, str], str] | None = None) -> None:
        self._by_workload: dict[tuple[str, str], list[Signal]] = defaultdict(list)
        for signal in signals:
            self._by_workload[(signal.namespace, signal.workload)].append(signal)
        for items in self._by_workload.values():
            items.sort(key=lambda s: s.ts)
        self._pod_workloads = pod_workloads or {}

    def workload(self, namespace: str, pod: str) -> str:
        return self._pod_workloads.get((namespace, pod)) or workload_of(pod)

    def for_window(self, namespace: str, pod: str, start: float, end: float, slack: float = 15.0) -> list[Signal]:
        """Signals on this pod's workload with start - slack <= ts <= end.

        Workload-level on purpose: when a pod is killed, the incident belongs to
        the Deployment, and its surviving or replacement pods are where the
        user (and the window labels) look.
        """
        wl = self.workload(namespace, pod)
        return [s for s in self._by_workload.get((namespace, wl), []) if start - slack <= s.ts <= end]
