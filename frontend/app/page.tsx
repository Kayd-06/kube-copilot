"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { diagnosePod, fetchUnhealthyPods } from "@/lib/api";
import type { DiagnoseResponse, PodSummary } from "@/lib/types";

function reasonTone(reason: string | null): string {
  const value = (reason || "").toLowerCase();
  if (value.includes("oom")) return "bg-orange-500/15 text-orange-200 ring-orange-500/30";
  if (value.includes("image") || value.includes("pull"))
    return "bg-violet-500/15 text-violet-200 ring-violet-500/30";
  if (value.includes("crash") || value.includes("error") || value.includes("fail"))
    return "bg-rose-500/15 text-rose-200 ring-rose-500/30";
  if (value.includes("pending") || value.includes("schedul"))
    return "bg-amber-500/15 text-amber-200 ring-amber-500/30";
  return "bg-sky-500/15 text-sky-200 ring-sky-500/30";
}

function podKey(pod: PodSummary): string {
  return `${pod.namespace}/${pod.name}`;
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-white/10">
        <div
          className="h-full rounded-full bg-emerald-400"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono text-xs text-slate-300">{pct}%</span>
    </div>
  );
}

export default function HomePage() {
  const [pods, setPods] = useState<PodSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const [result, setResult] = useState<DiagnoseResponse | null>(null);
  const [diagError, setDiagError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const data = await fetchUnhealthyPods();
      setPods(data.pods);
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to load pods");
      setPods([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onDiagnose = async (pod: PodSummary) => {
    setSelectedKey(podKey(pod));
    setDiagnosing(true);
    setDiagError(null);
    setResult(null);
    try {
      const data = await diagnosePod(pod.namespace, pod.name);
      setResult(data);
    } catch (err) {
      setDiagError(err instanceof Error ? err.message : "Diagnosis failed");
    } finally {
      setDiagnosing(false);
    }
  };

  const copyCommand = async (command: string) => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(command);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // Clipboard may be unavailable over plain HTTP on some browsers.
    }
  };

  const selectedLabel = useMemo(() => selectedKey, [selectedKey]);

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-8">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-[0.2em] text-emerald-400">
            Phase 1
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-white">Kube Copilot</h1>
          <p className="mt-2 max-w-xl text-sm text-slate-400">
            Unhealthy pods from your cluster, explained by an LLM. Connect a kind cluster,
            break a few demo workloads, and press Diagnose.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-lg bg-white/5 px-3 py-2 text-sm text-slate-200 ring-1 ring-white/10 transition hover:bg-white/10"
        >
          Refresh
        </button>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <section className="rounded-2xl bg-[#0c1220] ring-1 ring-white/8">
          <div className="flex items-center justify-between border-b border-white/8 px-5 py-4">
            <h2 className="text-sm font-medium text-slate-200">Unhealthy pods</h2>
            <span className="font-mono text-xs text-slate-500">{pods.length}</span>
          </div>

          {loading && (
            <p className="px-5 py-10 text-sm text-slate-400">Scanning the cluster…</p>
          )}

          {!loading && listError && (
            <div className="px-5 py-8 text-sm text-rose-300">
              <p className="font-medium">Could not reach the API</p>
              <p className="mt-2 text-rose-200/80">{listError}</p>
              <p className="mt-3 text-xs text-slate-500">
                Is the backend running on http://localhost:8000? Start it with{" "}
                <code className="font-mono text-slate-400">make backend-dev</code>.
              </p>
            </div>
          )}

          {!loading && !listError && pods.length === 0 && (
            <p className="px-5 py-10 text-sm text-slate-400">
              No unhealthy pods right now. Run{" "}
              <code className="font-mono text-slate-300">make demo-break</code> to deploy
              a few broken workloads in the <code className="font-mono">copilot-demo</code>{" "}
              namespace.
            </p>
          )}

          {!loading && !listError && pods.length > 0 && (
            <ul className="divide-y divide-white/6">
              {pods.map((pod) => {
                const key = podKey(pod);
                const active = key === selectedLabel;
                return (
                  <li
                    key={key}
                    className={`flex flex-wrap items-center gap-3 px-5 py-4 ${
                      active ? "bg-white/4" : ""
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-sm text-white">
                        {pod.namespace}/{pod.name}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {pod.phase}
                        {pod.restart_count > 0 ? ` · ${pod.restart_count} restarts` : ""}
                        {pod.node ? ` · ${pod.node}` : ""}
                      </p>
                    </div>
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${reasonTone(
                        pod.reason,
                      )}`}
                    >
                      {pod.reason || pod.phase}
                    </span>
                    <button
                      type="button"
                      disabled={diagnosing && active}
                      onClick={() => void onDiagnose(pod)}
                      className="rounded-lg bg-emerald-400 px-3 py-1.5 text-sm font-medium text-emerald-950 transition hover:bg-emerald-300 disabled:opacity-60"
                    >
                      {diagnosing && active ? "Diagnosing…" : "Diagnose"}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="rounded-2xl bg-[#0c1220] ring-1 ring-white/8">
          <div className="border-b border-white/8 px-5 py-4">
            <h2 className="text-sm font-medium text-slate-200">Diagnosis</h2>
          </div>
          <div className="px-5 py-5">
            {!selectedKey && !diagnosing && !result && !diagError && (
              <p className="text-sm text-slate-400">
                Pick a pod and press Diagnose. The copilot will send status, events, and
                recent logs to the LLM and return a root cause plus kubectl commands.
              </p>
            )}

            {diagnosing && (
              <p className="text-sm text-slate-300">
                Collecting logs and events, then asking the model…
              </p>
            )}

            {diagError && (
              <p className="text-sm text-rose-300">{diagError}</p>
            )}

            {result && !diagnosing && (
              <div className="space-y-5">
                <div className="flex items-center justify-between gap-3">
                  <p className="font-mono text-xs text-slate-400">
                    {result.pod.namespace}/{result.pod.name}
                  </p>
                  <ConfidenceBar value={result.diagnosis.confidence} />
                </div>

                <Block title="Summary" body={result.diagnosis.summary} />
                <Block title="Likely root cause" body={result.diagnosis.likely_root_cause} />
                <Block title="Suggested fix" body={result.diagnosis.suggested_fix} />

                <div>
                  <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
                    kubectl commands
                  </h3>
                  <ul className="space-y-2">
                    {result.diagnosis.kubectl_commands.map((command) => (
                      <li key={command}>
                        <button
                          type="button"
                          onClick={() => void copyCommand(command)}
                          className="block w-full rounded-lg bg-black/40 px-3 py-2 text-left font-mono text-xs text-emerald-200 ring-1 ring-white/10 transition hover:bg-black/60"
                          title="Copy to clipboard"
                        >
                          {command}
                          {copied === command ? (
                            <span className="ml-2 text-emerald-400">copied</span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>

                {result.logs.previous && (
                  <details className="rounded-lg bg-black/30 ring-1 ring-white/8">
                    <summary className="cursor-pointer px-3 py-2 text-xs text-slate-400">
                      Previous container logs
                    </summary>
                    <pre className="max-h-48 overflow-auto px-3 pb-3 font-mono text-[11px] leading-5 text-slate-300 whitespace-pre-wrap">
                      {result.logs.previous}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function Block({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-slate-500">
        {title}
      </h3>
      <p className="text-sm leading-6 text-slate-200">{body}</p>
    </div>
  );
}
