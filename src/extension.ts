import * as vscode from "vscode";

// ---------------------------------------------------------------------------
// MCP JSON-RPC helpers
// ---------------------------------------------------------------------------

type JsonRpcResponse<T> = {
  id?: string | number | null;
  result?: T;
  error?: { code: number; message: string; data?: unknown };
};

const MCP_HEADERS = {
  "Content-Type": "application/json",
  "Accept": "application/json, text/event-stream",
  "MCP-Protocol-Version": "2024-11-05",
};

function getConfig() {
  const cfg = vscode.workspace.getConfiguration("kbsearch");
  return {
    mcpUrl: cfg.get<string>("mcpUrl", "http://localhost:8080/mcp"),
    toolName: cfg.get<string>("toolName", "search_knowledge_base"),
    profile: cfg.get<string>("profile", "default"),
    topK: cfg.get<number>("topK", 5),
  };
}

/** Send a JSON-RPC request and return the parsed response. */
async function rpc<T>(
  url: string,
  payload: object,
  headers: Record<string, string>,
  signal: AbortSignal,
): Promise<{ body: JsonRpcResponse<T>; response: Response }> {
  const response = await fetch(url, {
    method: "POST",
    headers: { ...MCP_HEADERS, ...headers },
    body: JSON.stringify(payload),
    signal,
  });

  const raw = await response.text();
  if (!raw.trim()) {
    return { body: {}, response };
  }

  const ct = (response.headers.get("content-type") || "").toLowerCase();
  const isSse = ct.includes("text/event-stream") || raw.startsWith("event:") || raw.includes("\nevent:");

  const body: JsonRpcResponse<T> = isSse
    ? parseSsePayload(raw, (payload as { id?: unknown }).id)
    : JSON.parse(raw);

  return { body, response };
}

/** Extract the first (or id-matched) JSON-RPC message from an SSE stream. */
function parseSsePayload<T>(text: string, expectedId?: unknown): JsonRpcResponse<T> {
  for (const block of text.split(/\r?\n\r?\n/)) {
    const data = block
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart())
      .join("\n")
      .trim();

    if (!data || data === "[DONE]") { continue; }

    try {
      const obj = JSON.parse(data) as JsonRpcResponse<T>;
      if (expectedId === undefined || obj.id === expectedId) { return obj; }
    } catch { /* skip non-JSON */ }
  }
  throw new Error("MCP SSE response contained no JSON-RPC payload");
}

/** Extract text content from an MCP tool result. */
function extractText(result: unknown): string {
  if (typeof result === "string") { return result; }
  if (!result || typeof result !== "object") { return JSON.stringify(result); }

  const content = (result as { content?: unknown[] }).content;
  if (Array.isArray(content)) {
    const parts = content
      .map((c) => (c && typeof c === "object" && "text" in c ? String((c as { text: unknown }).text) : ""))
      .filter(Boolean);
    if (parts.length) { return parts.join("\n\n"); }
  }

  return JSON.stringify(result, null, 2);
}

// ---------------------------------------------------------------------------
// MCP session: initialize → notify → tools/call
// ---------------------------------------------------------------------------

async function callMcpTool(
  mcpUrl: string,
  toolName: string,
  toolArgs: Record<string, unknown>,
  signal: AbortSignal,
): Promise<string> {
  const id = Date.now();

  // 1. Initialize
  const init = await rpc<unknown>(mcpUrl, {
    jsonrpc: "2.0", id, method: "initialize",
    params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "kbsearch-extension", version: "0.0.1" } },
  }, {}, signal);

  if (!init.response.ok || init.body.error) {
    throw new Error(`MCP initialize failed: ${init.body.error?.message ?? init.response.statusText}`);
  }

  const sessionId = init.response.headers.get("mcp-session-id") ?? "";
  const sh: Record<string, string> = sessionId ? { "mcp-session-id": sessionId } : {};

  // 2. Notify initialized
  await fetch(mcpUrl, { method: "POST", headers: { ...MCP_HEADERS, ...sh }, body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }), signal });

  // 3. Call tool
  const call = await rpc<unknown>(mcpUrl, {
    jsonrpc: "2.0", id: id + 1, method: "tools/call",
    params: { name: toolName, arguments: toolArgs },
  }, sh, signal);

  if (!call.response.ok || call.body.error) {
    throw new Error(`MCP tools/call failed: ${call.body.error?.message ?? call.response.statusText}`);
  }

  return extractText(call.body.result);
}

// ---------------------------------------------------------------------------
// Response classification & Copilot summarisation
// ---------------------------------------------------------------------------

const NO_RESULTS_PATTERNS = [
  "No relevant information found",
  "no chunks met min relevance",
];

const ERROR_PREFIX = "ERROR:";

/** Returns true when the MCP tool response indicates no knowledge-base hits. */
function hasNoResults(text: string): boolean {
  if (text.startsWith(ERROR_PREFIX)) { return true; }
  return NO_RESULTS_PATTERNS.some((p) => text.includes(p));
}

async function summarize(
  request: vscode.ChatRequest,
  context: string,
  token: vscode.CancellationToken,
): Promise<string> {
  const messages = [
    vscode.LanguageModelChatMessage.User(
      "You are summarizing retrieved documentation context for the user. " +
      "Provide a concise, direct answer based ONLY on the retrieved context below. " +
      "Do NOT answer from your own general knowledge. " +
      "Use inline citations [n] when supported by the context. " +
      "Always preserve the SOURCES section and Query ID in the final output. " +
      "Do not repeat full chunk contents.",
    ),
    vscode.LanguageModelChatMessage.User(
      `User question: ${request.prompt}\n\nRetrieved context:\n${context}`,
    ),
  ];

  const resp = await request.model.sendRequest(messages, {}, token);
  let text = "";
  for await (const fragment of resp.text) { text += fragment; }
  return text.trim();
}

// ---------------------------------------------------------------------------
// Extension activation
// ---------------------------------------------------------------------------

export function activate(ctx: vscode.ExtensionContext) {
  const handler: vscode.ChatRequestHandler = async (request, _chatCtx, stream, token) => {
    const { mcpUrl, toolName, profile, topK } = getConfig();
    const controller = new AbortController();
    const sub = token.onCancellationRequested(() => controller.abort());

    try {
      const raw = await callMcpTool(
        mcpUrl, toolName,
        { query: request.prompt, profile, top_k: topK },
        controller.signal,
      );

      if (hasNoResults(raw)) {
        stream.markdown("This information is not available in the internal knowledge base.");
      } else {
        try {
          const summary = await summarize(request, raw, token);
          stream.markdown(summary || raw);
        } catch {
          stream.markdown(raw);
        }
      }
    } catch (err) {
      stream.markdown(`@knowledge MCP call failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      sub.dispose();
    }

    return { metadata: { participant: "knowledge" } };
  };

  const participant = vscode.chat.createChatParticipant("kbsearch-extension.knowledge", handler);
  participant.iconPath = vscode.Uri.joinPath(ctx.extensionUri, "media", "knowledge.svg");
  ctx.subscriptions.push(participant);
}

export function deactivate() {}
