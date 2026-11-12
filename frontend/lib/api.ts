import type { DiagnoseResponse, IncidentSaved, UnhealthyPodsResponse } from "./types";

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

export type DiagnoseStreamHandlers = {
  onContext?: (data: Pick<DiagnoseResponse, "pod" | "events" | "logs">) => void;
  onCitations?: (data: { citations: DiagnoseResponse["citations"]; knowledge_unavailable?: boolean }) => void;
  onToken?: (text: string) => void;
  onDiagnosis?: (data: DiagnoseResponse["diagnosis"]) => void;
  onWarning?: (detail: string) => void;
  onError?: (detail: string) => void;
};

function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const rawLine of block.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

export async function diagnosePodStream(
  namespace: string,
  name: string,
  handlers: DiagnoseStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/pods/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/diagnose?stream=true`,
    { cache: "no-store", signal, headers: { Accept: "text/event-stream" } },
  );
  if (!response.ok) throw new Error(await readError(response));
  if (!response.body) throw new Error("The diagnosis stream was empty");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (event: string, raw: string) => {
    let parsed: unknown = raw;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // token events are already JSON, but be tolerant of plain text
    }
    if (event === "token") {
      const text =
        typeof parsed === "object" && parsed !== null && "text" in parsed
          ? String((parsed as { text: unknown }).text ?? "")
          : typeof parsed === "string"
            ? parsed
            : "";
      if (text) handlers.onToken?.(text);
      return;
    }
    if (event === "context" && parsed && typeof parsed === "object") {
      handlers.onContext?.(parsed as DiagnoseResponse);
      const ctx = parsed as { citations?: DiagnoseResponse["citations"]; knowledge_unavailable?: boolean };
      if (ctx.citations) {
        handlers.onCitations?.({
          citations: ctx.citations,
          knowledge_unavailable: ctx.knowledge_unavailable,
        });
      }
      return;
    }
    if (event === "citations" && parsed && typeof parsed === "object") {
      handlers.onCitations?.(parsed as { citations: DiagnoseResponse["citations"]; knowledge_unavailable?: boolean });
      return;
    }
    if (event === "diagnosis" && parsed && typeof parsed === "object") {
      handlers.onDiagnosis?.(parsed as DiagnoseResponse["diagnosis"]);
      return;
    }
    if (event === "warning") {
      const detail =
        typeof parsed === "object" && parsed !== null && "detail" in parsed
          ? String((parsed as { detail: unknown }).detail ?? "")
          : "";
      handlers.onWarning?.(detail);
      return;
    }
    if (event === "error") {
      const detail =
        typeof parsed === "object" && parsed !== null && "detail" in parsed
          ? String((parsed as { detail: unknown }).detail ?? "Diagnosis failed")
          : "Diagnosis failed";
      handlers.onError?.(detail);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let separator: number;
    while ((separator = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      const parsed = parseSseBlock(block);
      if (parsed) dispatch(parsed.event, parsed.data);
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const parsed = parseSseBlock(buffer);
    if (parsed) dispatch(parsed.event, parsed.data);
  }
}

export async function saveIncident(payload: {
  pod_name: string;
  namespace: string;
  reason?: string | null;
  root_cause: string;
  fix: string;
  notes?: string;
}): Promise<IncidentSaved> {
  const response = await fetch(`${API_BASE}/incidents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}
