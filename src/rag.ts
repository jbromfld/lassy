/** Direct retrieval contract and presentation, independent of VS Code. */
export type RagChunk = {
  chunk_id?: string;
  citation?: string;
  content: string;
  title: string;
  url?: string;
  score?: number;
};

export type RagQueryResponse = {
  query_id: string;
  feedback_available?: boolean;
  chunks: RagChunk[];
  metrics?: { latency_ms?: number; chunks_retrieved?: number };
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function validateRagResponse(value: unknown): RagQueryResponse {
  const optionalString = (v: unknown) => v === undefined || typeof v === "string";
  const optionalScore = (v: unknown) => v === undefined || (typeof v === "number" && Number.isFinite(v));
  const optionalNumber = (v: unknown) => optionalScore(v) && (v === undefined || (v as number) >= 0);
  if (!record(value) || typeof value.query_id !== "string" || !value.query_id.trim() ||
      !Array.isArray(value.chunks) ||
      !(value.feedback_available === undefined || typeof value.feedback_available === "boolean") ||
      !value.chunks.every((c: unknown) => record(c) && typeof c.content === "string" &&
        typeof c.title === "string" && optionalString(c.url) && optionalString(c.chunk_id) &&
        (c.citation === undefined || (typeof c.citation === "string" && /^\[[1-9]\d*\]$/.test(c.citation))) && optionalScore(c.score)) ||
      !(value.metrics === undefined || (record(value.metrics) && optionalNumber(value.metrics.latency_ms) && optionalNumber(value.metrics.chunks_retrieved)))) {
    throw new Error("Malformed RAG retrieval response");
  }
  return value as RagQueryResponse;
}

export async function callRagDirect(url: string, query: string, signal: AbortSignal): Promise<RagQueryResponse> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({ query, retrieve_only: true }),
    signal,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`RAG query failed (${response.status}): ${body || response.statusText}`);
  }
  return validateRagResponse(await response.json());
}

const citation = (chunk: RagChunk, index: number) => chunk.citation ?? `[${index + 1}]`;
const plain = (value: string) => value.replace(/[\r\n]/g, " ").replace(/[\\`*_{}\[\]<>]/g, "\\$&");

export function retrievalContext(data: RagQueryResponse): string {
  return data.chunks.map((chunk, i) => `${citation(chunk, i)} ${chunk.title}\n${chunk.content}`).join("\n\n");
}

export function retrievalDetails(data: RagQueryResponse, includeSources: boolean): string {
  const parts: string[] = [];
  if (includeSources && data.chunks.length) {
    parts.push("### SOURCES", ...data.chunks.map((chunk, i) => {
      const url = chunk.url && /^https?:\/\//i.test(chunk.url)
        ? ` — <${chunk.url.replace(/[<>\s]/g, encodeURIComponent)}>` : "";
      return `- ${citation(chunk, i)} ${plain(chunk.title)}${url}`;
    }), "");
  }
  parts.push(`Query ID: ${plain(data.query_id)}${data.feedback_available === false ? " (feedback unavailable)" : " (use this for feedback)"}`);
  if (data.metrics?.latency_ms !== undefined) {
    parts.push(`Retrieved ${data.chunks.length} chunks in ${Math.round(data.metrics.latency_ms)}ms`);
  }
  return parts.join("\n");
}

/** Always append service-owned sources/details; generation failure falls back to context. */
export async function renderRetrieval(
  data: RagQueryResponse,
  includeSources: boolean,
  generate: (context: string) => Promise<string>,
  cancelled: () => boolean,
): Promise<string> {
  if (cancelled()) { return ""; }
  const context = retrievalContext(data);
  let answer = "This information is not available in the internal knowledge base.";
  if (data.chunks.length) {
    try { answer = (await generate(context)).trim() || context; }
    catch { answer = context; }
  }
  if (cancelled()) { return ""; }
  return `${answer}\n\n${retrievalDetails(data, includeSources)}`;
}
