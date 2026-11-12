"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { diagnosePodStream, fetchUnhealthyPods, saveIncident } from "@/lib/api";
import type { Citation, DiagnoseResponse, Diagnosis, PodSummary } from "@/lib/types";

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
  const [liveText, setLiveText] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [knowledgeUnavailable, setKnowledgeUnavailable] = useState(false);
  const [savingIncident, setSavingIncident] = useState(false);
  const [incidentSaved, setIncidentSaved] = useState(false);
  const [incidentError, setIncidentError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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
    return () => abortRef.current?.abort();
  }, [load]);

  const onDiagnose = async (pod: PodSummary) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setSelectedKey(podKey(pod));
    setDiagnosing(true);
    setDiagError(null);
    setResult(null);
    setLiveText("");
    setCitations([]);
    setKnowledgeUnavailable(false);
    setIncidentSaved(false);
    setIncidentError(null);

    let nextDiagnosis: Diagnosis | null = null;
    let nextPod = pod;
    let nextEvents: DiagnoseResponse["events"] = [];
    let nextLogs: DiagnoseResponse["logs"] = { current: "", previous: null, container: null };
    let nextCitations: Citation[] = [];
    let nextUnavailable = false;

    try {
      await diagnosePodStream(
        pod.namespace,
        pod.name,
        {
          onContext: (data) => {
            nextPod = data.pod;
            nextEvents = data.events;
            nextLogs = data.logs;
          },
          onCitations: (data) => {
            nextCitations = data.citations || [];
            nextUnavailable = Boolean(data.knowledge_unavailable);
            setCitations(nextCitations);
            setKnowledgeUnavailable(nextUnavailable);
          },
          onToken: (text) => {
            setLiveText((current) => current + text);
          },
          onDiagnosis: (data) => {
            nextDiagnosis = data;
          },
          onWarning: () => {
            nextUnavailable = true;
            setKnowledgeUnavailable(true);
          },
          onError: (detail) => {
            setDiagError(detail);
          },
        },
        controller.signal,
      );
      if (nextDiagnosis) {
        setResult({
          pod: nextPod,
          events: nextEvents,
          logs: nextLogs,
          diagnosis: nextDiagnosis,
          citations: nextCitations,
          knowledge_unavailable: nextUnavailable,
        });
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setDiagError(err instanceof Error ? err.message : "Diagnosis failed");
    } finally {
      if (!controller.signal.aborted) setDiagnosing(false);
    }
  };

  const onSaveIncident = async () => {
    if (!result) return;
    setSavingIncident(true);
    setIncidentError(null);
    try {
      await saveIncident({
        pod_name: result.pod.name,
        namespace: result.pod.namespace,
        reason: result.pod.reason,
        root_cause: result.diagnosis.likely_root_cause,
        fix: result.diagnosis.suggested_fix,
      });
      setIncidentSaved(true);
    } catch (err) {
      setIncidentError(err instanceof Error ? err.message : "Could not save the incident");
    } finally {
      setSavingIncident(false);
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
  const shownCitations = result?.citations?.length ? result.citations : citations;

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-8">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-[0.2em] text-emerald-400">
            Phase 2 · RAG
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-white">Kube Copilot</h1>
          <p className="mt-2 max-w-xl text-sm text-slate-400">
            Unhealthy pods from your cluster, explained by an LLM that cites runbooks
            and past incidents. Connect a kind cluster, ingest the knowledge base, and
            press Diagnose.
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
                Pick a pod and press Diagnose. The copilot will stream a root-cause
                explanation, then list the runbooks and past incidents it cited.
              </p>
            )}

            {diagnosing && (
              <div className="space-y-3">
                <p className="text-sm text-slate-300">
                  Collecting logs and events, retrieving runbooks, then asking the model…
                </p>
                {liveText ? (
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-black/40 px-3 py-2 font-mono text-[11px] leading-5 text-emerald-100/90 ring-1 ring-white/8">
                    {liveText}
                  </pre>
                ) : (
                  <p className="text-xs text-slate-500">Waiting for the first tokens…</p>
                )}
              </div>
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

                {(knowledgeUnavailable || result.knowledge_unavailable) && (
                  <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200 ring-1 ring-amber-500/20">
                    Knowledge store was unavailable. This diagnosis was made from pod
                    evidence only, without runbook citations. Start Qdrant with{" "}
                    <code className="font-mono">make qdrant-up</code> and ingest runbooks
                    with <code className="font-mono">make ingest</code>.
                  </p>
                )}

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

                <CitationsList citations={shownCitations} />

                <div>
                  <button
                    type="button"
                    onClick={() => void onSaveIncident()}
                    disabled={savingIncident || incidentSaved}
                    className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-slate-200 ring-1 ring-white/10 transition hover:bg-white/10 disabled:opacity-60"
                  >
                    {incidentSaved
                      ? "Saved to incident memory"
                      : savingIncident
                        ? "Saving…"
                        : "Save as resolved incident"}
                  </button>
                  {incidentError ? (
                    <p className="mt-2 text-xs text-rose-300">{incidentError}</p>
                  ) : null}
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

function CitationsList({ citations }: { citations: Citation[] }) {
  if (!citations.length) {
    return (
      <div>
        <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-slate-500">
          Citations
        </h3>
        <p className="text-xs text-slate-500">
          No runbook or incident matches were retrieved for this diagnosis.
        </p>
      </div>
    );
  }
  return (
    <div>
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
        Citations
      </h3>
      <ul className="space-y-2">
        {citations.map((citation, index) => (
          <li
            key={`${citation.source}-${citation.section}-${index}`}
            className="rounded-lg bg-black/30 px-3 py-2 ring-1 ring-white/8"
          >
            <p className="text-sm text-slate-200">
              {citation.title}
              {citation.section && citation.section !== citation.title ? (
                <span className="text-slate-400"> — {citation.section}</span>
              ) : null}
            </p>
            <p className="mt-1 font-mono text-[11px] text-slate-500">
              {citation.source}
              {citation.kind ? ` · ${citation.kind}` : ""}
              {typeof citation.score === "number" ? ` · score ${citation.score.toFixed(2)}` : ""}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
