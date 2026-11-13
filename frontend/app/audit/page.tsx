"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { describeAction, statusTone } from "@/components/SafeActions";
import { fetchActions, fetchAudit } from "@/lib/api";
import type { ActionRecord, AuditEvent } from "@/lib/types";

export default function AuditPage() {
  const [actions, setActions] = useState<ActionRecord[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, e] = await Promise.all([fetchActions(100), fetchAudit(300)]);
      setActions(a.actions);
      setEvents(e.events);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-8">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-[0.2em] text-emerald-400">Safe actions</p>
          <h1 className="text-3xl font-semibold tracking-tight text-white">Audit log</h1>
          <p className="mt-2 text-sm text-slate-400">
            Every proposal, policy block, approval, rejection, execution and verification.
          </p>
        </div>
        <Link href="/" className="text-sm text-emerald-300 hover:underline">
          ← Back to pods
        </Link>
      </header>
      {error && <p className="mb-4 text-sm text-rose-300">{error}</p>}

      <section className="mb-6 rounded-2xl bg-[#0c1220] ring-1 ring-white/8">
        <h2 className="border-b border-white/8 px-5 py-4 text-sm font-medium text-slate-200">Actions</h2>
        <table className="w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="px-5 py-2 font-medium">Created</th>
              <th className="px-2 py-2 font-medium">Action</th>
              <th className="px-2 py-2 font-medium">Status</th>
              <th className="px-2 py-2 font-medium">Decided by</th>
              <th className="px-2 py-2 font-medium">Recovery</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/6">
            {actions.map((a) => (
              <tr key={a.id} className="align-top">
                <td className="px-5 py-2 font-mono text-slate-500">{new Date(a.created_at).toLocaleString()}</td>
                <td className="px-2 py-2 text-slate-200">
                  {describeAction(a)}
                  {a.violations.length > 0 && (
                    <div className="mt-1 text-rose-300/80">{a.violations.join("; ")}</div>
                  )}
                </td>
                <td className="px-2 py-2">
                  <span className={`rounded-full px-2 py-0.5 ring-1 ${statusTone(a.status)}`}>{a.status}</span>
                </td>
                <td className="px-2 py-2 text-slate-400">{a.decided_by ?? "—"}</td>
                <td className="px-2 py-2 text-slate-400">
                  {a.verification?.time_to_recovery_seconds != null
                    ? `${a.verification.time_to_recovery_seconds}s`
                    : "—"}
                </td>
              </tr>
            ))}
            {actions.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-6 text-slate-500">
                  No actions yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="rounded-2xl bg-[#0c1220] ring-1 ring-white/8">
        <h2 className="border-b border-white/8 px-5 py-4 text-sm font-medium text-slate-200">Events</h2>
        <ul className="divide-y divide-white/6">
          {events.map((e) => (
            <li key={e.id} className="flex flex-wrap gap-x-3 px-5 py-2 text-xs">
              <span className="font-mono text-slate-500">{new Date(e.ts).toLocaleString()}</span>
              <span className="font-medium text-slate-200">{e.event}</span>
              <span className="text-slate-400">{e.actor}</span>
              {e.action_id && <span className="font-mono text-slate-500">{e.action_id}</span>}
              <span className="min-w-0 flex-1 truncate font-mono text-slate-500">{JSON.stringify(e.detail)}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
