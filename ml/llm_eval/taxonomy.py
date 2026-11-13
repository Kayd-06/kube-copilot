"""Root-cause categories, their deterministic matchers, and the remediation vocabulary.

A diagnosis has no category field; its free-text `likely_root_cause` is matched
against a fixed regex per category. A case is correct when the regex for its
hand-assigned category matches. `predict_category` (earliest match wins) is
only used for the confusion summary in the report.

The regexes were written from the category definitions before any model
output was looked at, then frozen; agreement with a hand check is reported.
"""

from __future__ import annotations

import re

CATEGORIES: dict[str, str] = {
    "image_pull": "The image cannot be pulled (bad tag, missing repo, unreachable or private registry).",
    "app_crash": "The process (or an init container) exits on its own: bad command/entrypoint or an application error.",
    "bad_config_value": "Configuration exists but a value in it (env var, config key) is invalid, so the app refuses to start.",
    "missing_config": "A referenced ConfigMap, Secret or key does not exist.",
    "oom_killed": "The container exceeds its memory limit and is OOMKilled.",
    "probe_failure": "A liveness/readiness probe fails (wrong path/port/command or too short a delay).",
    "cpu_throttling": "The CPU limit is too low, so the workload is throttled and slow.",
    "unschedulable": "The pod cannot be scheduled (resource requests, nodeSelector, affinity).",
    "pvc_pending": "A PersistentVolumeClaim is pending or missing, so the pod cannot start.",
    "dependency_connectivity": "The app cannot reach a dependency (Service port mismatch, DNS, upstream down).",
    "pod_disruption": "The pod was killed/deleted/evicted and replaced; the workload self-heals.",
    "healthy": "Nothing is wrong.",
}

_I = re.IGNORECASE
PATTERNS: dict[str, re.Pattern[str]] = {
    "image_pull": re.compile(
        r"pull|registry|manifest unknown|"
        r"(image|tag)\b.{0,60}(not (be )?found|does not exist|doesn't exist|non-?existent|invalid|incorrect|wrong|typo|"
        r"unavailable|denied|unauthori[sz]ed|private|misspel|cannot be (fetched|resolved|downloaded))",
        _I,
    ),
    "app_crash": re.compile(
        r"crash|exit(s|ed|ing)?\b.{0,30}(code|status|immediately|with)|exit code|non-?zero|command|entrypoint|"
        r"executable|no such file|\$PATH|panic|exception|segfault|bug|init.?container|migration|start(up)? script",
        _I,
    ),
    "bad_config_value": re.compile(
        r"(invalid|bad|wrong|incorrect|unsupported|unknown|malformed|misconfigured|unrecogni[sz]ed|non-?numeric|illegal)"
        r".{0,60}(env|variable|config|setting|value|port|log.?level|parameter|option)|"
        r"(env(ironment)? var\w*|config(uration)? (value|key|option)|setting|\bPORT\b|LOG_LEVEL).{0,60}"
        r"(invalid|bad|wrong|incorrect|unsupported|unknown|malformed|not (a )?valid|non-?numeric|rejected)",
        _I,
    ),
    "missing_config": re.compile(
        r"(config ?map|secret).{0,80}(missing|not found|does not exist|doesn't exist|absent|not exist|"
        r"couldn't find|cannot find|could not be found|can't be found|lacks|without|non-?existent|not present|nonexist)|"
        r"(missing|non-?existent|absent|undefined).{0,40}(config ?map|secret|key)|createcontainerconfigerror|"
        r"couldn't find key",
        _I,
    ),
    "oom_killed": re.compile(
        r"\boom|out of memory|out-of-memory|memory limit|exceed\w* (its |the )?(container'?s? )?memory|"
        r"memory (leak|exhaust|pressure|usage exceed|consumption)|insufficient memory limit",
        _I,
    ),
    "probe_failure": re.compile(r"probe|health ?check", _I),
    "cpu_throttling": re.compile(
        r"throttl|cpu.{0,50}(limit|starv|insufficient|too low|constrain|saturat|bound|quota|contention)", _I
    ),
    "unschedulable": re.compile(
        r"unschedul|insufficient (cpu|memory)|node ?selector|no (suitable |available |matching )?nodes?|"
        r"(cannot|can't|could not|unable to) be scheduled|failedscheduling|affinity|not (been )?scheduled|"
        r"request\w*.{0,60}(exceed|more than|too (high|large)|larger than|cannot be satisfied)",
        _I,
    ),
    "pvc_pending": re.compile(
        r"persistentvolumeclaim|\bpvcs?\b|storage ?class|volume claim|persistent volume", _I
    ),
    "dependency_connectivity": re.compile(
        r"connect|refused|unreachable|\bdns\b|resolv|upstream|dependen|endpoint|targetport|port mismatch|"
        r"network|service.{0,40}(port|not listening|wrong)",
        _I,
    ),
    "pod_disruption": re.compile(
        r"(pod|container|replica).{0,50}(killed|deleted|terminated|evicted|replaced|recreated|restarted by)|"
        r"\bkill(ed|ing)?\b|chaos|disrupt|evict|deleted|preempt",
        _I,
    ),
    "healthy": re.compile(
        r"\bno (actual |real |apparent |current |active )?(issue|problem|fault|error|failure|incident|root cause|"
        r"evidence of (a |any )?(problem|failure|issue|fault))|healthy|running normally|operating normally|"
        r"functioning (normally|correctly|as expected)|nothing (is )?wrong|not failing|completed successfully|"
        r"succeeded|working as expected",
        _I,
    ),
}

# Remediation vocabulary = the safe-actions tool names.
ACTIONS = ("rollback_deployment", "restart_deployment", "scale_deployment", "delete_pod", "no_action")


def matches(category: str, text: str) -> bool:
    return bool(PATTERNS[category].search(text or ""))


def predict_category(text: str) -> str | None:
    """Earliest-matching category in `text`; ties go to the order in CATEGORIES."""
    best: tuple[int, int, str] | None = None
    for order, (name, pattern) in enumerate(PATTERNS.items()):
        m = pattern.search(text or "")
        if m and (best is None or (m.start(), order) < best[:2]):
            best = (m.start(), order, name)
    return best[2] if best else None
