import type { DiagnoseResponse, UnhealthyPodsResponse } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

export async function fetchUnhealthyPods(): Promise<UnhealthyPodsResponse> {
  const response = await fetch(`${API_BASE}/pods/unhealthy`, { cache: "no-store" });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function diagnosePod(namespace: string, name: string): Promise<DiagnoseResponse> {
  const response = await fetch(
    `${API_BASE}/pods/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/diagnose`,
    { cache: "no-store" },
  );
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}
