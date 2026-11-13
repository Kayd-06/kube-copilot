"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  approveAction,
  fetchAction,
  fetchActionsConfig,
  proposeActions,
  rejectAction,
  revertAction,
} from "@/lib/api";
import type { ActionDetail, ActionRecord, ActionStatus, ActionsConfig, AuditEvent } from "@/lib/types";

const ACTOR_KEY = "kube-copilot.actor";

export function statusTone(status: ActionStatus): string {
  switch (status) {
    case "proposed":
      return "bg-sky-500/15 text-sky-200 ring-sky-500/30";
    case "resolved":
      return "bg-emerald-500/15 text-emerald-200 ring-emerald-500/30";
    case "executing":
    case "verifying":
      return "bg-amber-500/15 text-amber-200 ring-amber-500/30";
    case "blocked":
    case "failed":
    case "not_resolved":
      return "bg-rose-500/15 text-rose-200 ring-rose-500/30";
    default:
      return "bg-white/5 text-slate-300 ring-white/10";
  }
}

export function describeAction(a: Pick<ActionRecord, "kind" | "params">): string {
  const ns = String(a.params.namespace ?? "?");
  const name = String(a.params.name ?? "?");
  switch (a.kind) {
    case "rollback_deployment":
      return `Roll back deployment ${ns}/${name} to its previous revision`;
    case "restart_deployment":
      return `Rolling restart of deployment ${ns}/${name}`;
    case "scale_deployment":
      return `Scale deployment ${ns}/${name} to ${String(a.params.replicas)} replicas`;
    case "delete_pod":
      return `Delete crashlooping pod ${ns}/${name} (its ReplicaSet recreates it)`;
    default:
      return `${a.kind || "(empty)"} — not an allowed action`;
  }
}

function DiffView({ diff }: { diff: string }) {
  if (!diff) return <p className="text-xs text-slate-500">No changes in the dry run.</p>;
  return (
    <pre className="max-h-64 overflow-auto rounded-lg bg-black/50 px-3 py-2 font-mono text-[11px] leading-5 ring-1 ring-white/8">
      {diff.split("\n").map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith("+") && !line.startsWith("+++")
              ? "text-emerald-300"
              : line.startsWith("-") && !line.startsWith("---")
                ? "text-rose-300"
                : "text-slate-400"
          }
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

export function Timeline({ events }: { events: AuditEvent[] }) {
  if (!events.length) return null;
  return (
    <ol className="space-y-1.5 border-l border-white/10 pl-3">
      {events.map((e) => (
        <li key={e.id} className="text-xs">
          <span className="font-mono text-slate-500">{new Date(e.ts).toLocaleTimeString()}</span>{" "}
          <span className="font-medium text-slate-200">{e.event.replaceAll("_", " ")}</span>{" "}
          <span className="text-slate-500">by {e.actor}</span>
          {typeof e.detail.reason === "string" && <span className="text-slate-400"> — {e.detail.reason}</span>}
          {typeof e.detail.error === "string" && <span className="text-rose-300"> — {e.detail.error}</span>}
        </li>
      ))}
    </ol>
  );
}

function ProposalCard({
  initial,
  actor,
  killSwitch,
  onRevertProposed,
}: {
  initial: ActionRecord;
  actor: string;
  killSwitch: boolean;
  onRevertProposed: (a: ActionRecord) => void;
}) {
  const [action, setAction] = useState<ActionDetail>({ ...initial, timeline: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setAction(await fetchAction(initial.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [initial.id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (action.status !== "executing" && action.status !== "verifying") return;
    const timer = setInterval(() => void refresh(), 2000);
    return () => clearInterval(timer);
  }, [action.status, refresh]);

  const decide = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      await refresh();
    }
  };

  const v = action.verification;
  const last = v?.samples[v.samples.length - 1];
  return (
    <div className="space-y-3 rounded-xl bg-black/25 p-4 ring-1 ring-white/8">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-white">{describeAction(action)}</p>
          {action.rationale && <p className="mt-1 text-xs text-slate-400">LLM: {action.rationale}</p>}
          {action.reverts_action_id && (
            <p className="mt-1 text-xs text-slate-500">Reverts action {action.reverts_action_id}</p>
          )}
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${statusTone(action.status)}`}>
          {action.status.replace("_", " ")}
        </span>
      </div>

      {action.violations.length > 0 && (
        <ul className="space-y-1 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-200 ring-1 ring-rose-500/20">
          {action.violations.map((x) => (
            <li key={x}>Policy: {x}</li>
          ))}
        </ul>
      )}

      {action.preview?.dry_run_ok && (
        <div>
          <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">
            Server-side dry run
          </h4>
          <DiffView diff={action.preview.diff} />
        </div>
      )}

      {action.status === "proposed" && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy || killSwitch || !actor}
            onClick={() => void decide(() => approveAction(action.id, actor))}
            className="rounded-lg bg-emerald-400 px-3 py-1.5 text-sm font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-50"
          >
            Approve &amp; run
          </button>
          <button
            type="button"
            disabled={busy || !actor}
            onClick={() => void decide(() => rejectAction(action.id, actor))}
            className="rounded-lg bg-white/5 px-3 py-1.5 text-sm text-slate-200 ring-1 ring-white/10 hover:bg-white/10 disabled:opacity-50"
          >
            Reject
          </button>
          {killSwitch && <span className="text-xs text-rose-300">Kill switch is on: nothing can run.</span>}
        </div>
      )}

      {v && (
        <div className="rounded-lg bg-white/3 px-3 py-2 text-xs ring-1 ring-white/8">
          <p className="text-slate-300">
            Verification:{" "}
            {v.resolved === null
              ? `watching (${last ? `${last.ready}/${last.desired} ready, t=${last.t}s` : "starting"})`
              : v.resolved
                ? `resolved in ${v.time_to_recovery_seconds}s`
                : "not resolved"}
          </p>
          {v.reason && <p className="mt-1 text-slate-500">{v.reason}</p>}
          {v.metric_anomalies && v.metric_anomalies.length > 0 && (
            <p className="mt-1 text-amber-200/80">
              Metric model still flags: {v.metric_anomalies.join(", ")} (informational)
            </p>
          )}
        </div>
      )}
      {action.status === "verifying" && !v && <p className="text-xs text-amber-200">Executed; verifying…</p>}
      {action.error && <p className="text-xs text-rose-300">{action.error}</p>}

      {action.revertible && (action.status === "resolved" || action.status === "not_resolved") && (
        <button
          type="button"
          disabled={busy || !actor}
          onClick={() =>
            void decide(async () => {
              onRevertProposed(await revertAction(action.id, actor));
            })
          }
          className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-slate-200 ring-1 ring-white/10 hover:bg-white/10 disabled:opacity-50"
        >
          Propose revert (needs its own approval)
        </button>
      )}

      {error && <p className="text-xs text-rose-300">{error}</p>}
      <details>
        <summary className="cursor-pointer text-xs text-slate-500">Timeline</summary>
        <div className="mt-2">
          <Timeline events={action.timeline} />
        </div>
      </details>
    </div>
  );
}

export function SafeActionsPanel({ namespace, pod }: { namespace: string; pod: string }) {
  const [config, setConfig] = useState<ActionsConfig | null>(null);
  const [actor, setActor] = useState("");
  const [actions, setActions] = useState<ActionRecord[]>([]);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setActor(window.localStorage.getItem(ACTOR_KEY) || "");
    fetchActionsConfig()
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);

  useEffect(() => {
    setActions([]);
    setNote("");
    setError(null);
  }, [namespace, pod]);

  const onPropose = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await proposeActions(namespace, pod, actor || "anonymous");
      setActions(res.actions);
      setNote(res.note);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3 rounded-xl bg-white/3 p-4 ring-1 ring-white/8">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wider text-slate-400">Safe actions</h3>
        <Link href="/audit" className="text-xs text-emerald-300 hover:underline">
          Audit log →
        </Link>
      </div>
      {config && !config.enabled ? (
        <p className="text-xs text-slate-400">
          Read-only mode. Remediation is off; enable it with <code className="font-mono">ACTIONS_ENABLED=true</code>{" "}
          (Helm: <code className="font-mono">actions.enabled=true</code>).
        </p>
      ) : (
        <>
          <p className="text-xs text-slate-500">
            The LLM can only propose: rollback, rolling restart, scale ({config?.scale_min ?? 1}–
            {config?.scale_max ?? 5}) or delete one crashlooping pod, in{" "}
            {config?.allowed_namespaces.join(", ") || "no namespaces"}. Policy is checked in code, every change is
            dry-run first, and nothing runs until you approve.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={actor}
              onChange={(e) => {
                setActor(e.target.value);
                window.localStorage.setItem(ACTOR_KEY, e.target.value);
              }}
              placeholder="Your name (recorded in audit)"
              className="w-56 rounded-lg bg-black/40 px-3 py-1.5 text-xs text-slate-200 ring-1 ring-white/10 placeholder:text-slate-600"
            />
            <button
              type="button"
              disabled={loading}
              onClick={() => void onPropose()}
              className="rounded-lg bg-sky-400 px-3 py-1.5 text-sm font-medium text-sky-950 hover:bg-sky-300 disabled:opacity-60"
            >
              {loading ? "Asking the LLM…" : "Propose remediation"}
            </button>
          </div>
          {config?.kill_switch && (
            <p className="text-xs text-rose-300">Global kill switch is ON: proposals only, no execution.</p>
          )}
        </>
      )}
      {error && <p className="text-xs text-rose-300">{error}</p>}
      {note && <p className="text-xs text-slate-300">{note}</p>}
      <div className="space-y-3">
        {actions.map((a) => (
          <ProposalCard
            key={a.id}
            initial={a}
            actor={actor}
            killSwitch={Boolean(config?.kill_switch)}
            onRevertProposed={(r) => setActions((prev) => [...prev, r])}
          />
        ))}
      </div>
    </div>
  );
}
