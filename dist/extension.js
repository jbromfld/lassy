"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
const os = require("os");
const path = require("path");
const fs_1 = require("fs");
const FIRST_RUN_SYNC_KEY = "kbsearch.firstRunSyncDone";
function getConfig() {
    const cfg = vscode.workspace.getConfiguration("kbsearch");
    return {
        mcpUrl: cfg.get("mcpUrl", "http://localhost:8080/mcp"),
        mcpServerName: cfg.get("mcpServerName", "cicd-mcp"),
        toolName: cfg.get("toolName", "search_knowledge_base"),
        githubUserHeader: cfg.get("githubUserHeader", ""),
        profile: cfg.get("profile", "default"),
        topK: cfg.get("topK", 5),
        includeSources: cfg.get("includeSources", true),
        summarizeWithCopilot: cfg.get("summarizeWithCopilot", true),
        syncMcpConfigOnInstall: cfg.get("syncMcpConfigOnInstall", true)
    };
}
function getUserMcpConfigPath() {
    const home = os.homedir();
    if (process.platform === "darwin") {
        return path.join(home, "Library", "Application Support", "Code", "User", "mcp.json");
    }
    if (process.platform === "win32") {
        const appData = process.env.APPDATA || path.join(home, "AppData", "Roaming");
        return path.join(appData, "Code", "User", "mcp.json");
    }
    return path.join(home, ".config", "Code", "User", "mcp.json");
}
async function readUserMcpConfig(filePath) {
    try {
        const text = await fs_1.promises.readFile(filePath, "utf8");
        const parsed = JSON.parse(text);
        return parsed && typeof parsed === "object" ? parsed : {};
    }
    catch {
        return {};
    }
}
async function writeUserMcpConfig(filePath, config) {
    await fs_1.promises.mkdir(path.dirname(filePath), { recursive: true });
    await fs_1.promises.writeFile(filePath, `${JSON.stringify(config, null, 2)}\n`, "utf8");
}
async function ensureFirstRunMcpSync(context) {
    const { mcpServerName, mcpUrl, syncMcpConfigOnInstall } = getConfig();
    if (!syncMcpConfigOnInstall) {
        return;
    }
    const alreadySynced = context.globalState.get(FIRST_RUN_SYNC_KEY, false);
    if (alreadySynced) {
        return;
    }
    const cfg = vscode.workspace.getConfiguration("kbsearch");
    const userMcpPath = getUserMcpConfigPath();
    const mcpConfig = await readUserMcpConfig(userMcpPath);
    const servers = mcpConfig.servers ?? {};
    const existingServer = servers[mcpServerName] ?? {};
    const configuredMcpUrl = cfg.inspect("mcpUrl");
    const hasExplicitMcpUrl = Boolean(configuredMcpUrl?.workspaceFolderValue
        || configuredMcpUrl?.workspaceValue
        || configuredMcpUrl?.globalValue);
    if (!hasExplicitMcpUrl && existingServer.url) {
        await cfg.update("mcpUrl", existingServer.url, vscode.ConfigurationTarget.Global);
    }
    if (!servers[mcpServerName]) {
        servers[mcpServerName] = {
            url: (existingServer.url || mcpUrl),
            type: "sse",
            description: "MCP server with RAG search and CI/CD query tools"
        };
        await writeUserMcpConfig(userMcpPath, {
            ...mcpConfig,
            servers,
            inputs: mcpConfig.inputs ?? []
        });
    }
    await context.globalState.update(FIRST_RUN_SYNC_KEY, true);
}
function getSessionHeaderValue(response) {
    return response.headers.get("mcp-session-id")
        ?? response.headers.get("Mcp-Session-Id")
        ?? response.headers.get("MCP-Session-Id")
        ?? "";
}
async function callJsonRpc(url, payload, headers, signal) {
    const response = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2024-11-05",
            ...headers
        },
        body: JSON.stringify(payload),
        signal
    });
    const body = await parseJsonRpcBody(response, getPayloadId(payload));
    return { body, response };
}
function getPayloadId(payload) {
    if (!payload || typeof payload !== "object") {
        return undefined;
    }
    if (!("id" in payload)) {
        return undefined;
    }
    const id = payload.id;
    if (typeof id === "string" || typeof id === "number" || id === null) {
        return id;
    }
    return undefined;
}
async function parseJsonRpcBody(response, expectedId) {
    const raw = await response.text();
    if (!raw.trim()) {
        return {};
    }
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    const isSse = contentType.includes("text/event-stream") || raw.includes("\nevent:") || raw.startsWith("event:");
    if (!isSse) {
        return JSON.parse(raw);
    }
    return parseJsonRpcFromSse(raw, expectedId);
}
function parseJsonRpcFromSse(sseText, expectedId) {
    const events = sseText.split(/\r?\n\r?\n/);
    const parsed = [];
    for (const eventBlock of events) {
        const lines = eventBlock.split(/\r?\n/);
        const dataLines = lines
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart());
        if (dataLines.length === 0) {
            continue;
        }
        const dataPayload = dataLines.join("\n").trim();
        if (!dataPayload || dataPayload === "[DONE]") {
            continue;
        }
        try {
            const obj = JSON.parse(dataPayload);
            parsed.push(obj);
        }
        catch {
            // Ignore non-JSON event payloads.
        }
    }
    if (parsed.length === 0) {
        throw new Error("MCP SSE response contained no JSON-RPC payload");
    }
    if (expectedId !== undefined) {
        const matched = parsed.find((item) => item.id === expectedId);
        if (matched) {
            return matched;
        }
    }
    return parsed[0];
}
function extractToolText(toolResult) {
    if (typeof toolResult === "string") {
        return toolResult;
    }
    if (!toolResult || typeof toolResult !== "object") {
        return JSON.stringify(toolResult);
    }
    const maybeContent = toolResult.content;
    if (Array.isArray(maybeContent)) {
        const textParts = maybeContent
            .map((item) => {
            if (typeof item === "string") {
                return item;
            }
            if (item && typeof item === "object" && "text" in item) {
                const text = item.text;
                return typeof text === "string" ? text : "";
            }
            return "";
        })
            .filter(Boolean);
        if (textParts.length > 0) {
            return textParts.join("\n\n");
        }
    }
    const maybeStructured = toolResult.structuredContent;
    if (maybeStructured !== undefined) {
        return JSON.stringify(maybeStructured, null, 2);
    }
    return JSON.stringify(toolResult, null, 2);
}
async function callMcpTool(mcpUrl, toolName, toolArgs, includeSources, githubUserHeader, signal) {
    const commonHeaders = {};
    if (githubUserHeader.trim()) {
        commonHeaders["X-GitHub-User"] = githubUserHeader.trim();
    }
    const initId = Date.now();
    const initPayload = {
        jsonrpc: "2.0",
        id: initId,
        method: "initialize",
        params: {
            protocolVersion: "2024-11-05",
            capabilities: {},
            clientInfo: {
                name: "kbsearch-extension",
                version: "0.0.1"
            }
        }
    };
    let init = await callJsonRpc(mcpUrl, initPayload, commonHeaders, signal);
    // Some MCP gateways are strict about Accept formatting; retry once with explicit order.
    if (init.response.status === 406) {
        init = await callJsonRpc(mcpUrl, initPayload, {
            ...commonHeaders,
            Accept: "text/event-stream, application/json"
        }, signal);
    }
    if (!init.response.ok) {
        const detail = init.body.error?.message ?? "Unknown error";
        throw new Error(`MCP initialize failed (${init.response.status}): ${detail}`);
    }
    if (init.body.error) {
        throw new Error(`MCP initialize error: ${init.body.error.message}`);
    }
    const sessionId = getSessionHeaderValue(init.response);
    const sessionHeaders = {
        ...commonHeaders,
        ...(sessionId ? { "mcp-session-id": sessionId } : {})
    };
    const initializedPayload = {
        jsonrpc: "2.0",
        method: "notifications/initialized",
        params: {}
    };
    await fetch(mcpUrl, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2024-11-05",
            ...sessionHeaders
        },
        body: JSON.stringify(initializedPayload),
        signal
    });
    const callId = initId + 1;
    const toolsListPayload = {
        jsonrpc: "2.0",
        id: callId,
        method: "tools/list",
        params: {}
    };
    const toolsList = await callJsonRpc(mcpUrl, toolsListPayload, sessionHeaders, signal);
    const supportsIncludeSources = Boolean(toolsList.body.result?.tools
        ?.find((tool) => tool.name === toolName)
        ?.inputSchema
        ?.properties
        ?.include_sources);
    const finalToolArgs = {
        ...toolArgs,
        ...(supportsIncludeSources ? { include_sources: includeSources } : {})
    };
    const callToolId = callId + 1;
    const toolCallPayload = {
        jsonrpc: "2.0",
        id: callToolId,
        method: "tools/call",
        params: {
            name: toolName,
            arguments: finalToolArgs
        }
    };
    const toolCall = await callJsonRpc(mcpUrl, toolCallPayload, sessionHeaders, signal);
    if (!toolCall.response.ok) {
        const detail = toolCall.body.error?.message ?? "Unknown error";
        throw new Error(`MCP tools/call failed (${toolCall.response.status}): ${detail}`);
    }
    if (toolCall.body.error) {
        throw new Error(`MCP tools/call error: ${toolCall.body.error.message}`);
    }
    return extractToolText(toolCall.body.result);
}
function formatToolOutput(text) {
    if (!text.trim()) {
        return "No response returned from MCP tool.";
    }
    return text;
}
async function summarizeResponseWithCopilot(request, retrievedContext, token) {
    const messages = [
        vscode.LanguageModelChatMessage.User([
            "You are summarizing retrieved documentation context for the user.",
            "Provide a concise, direct answer in 3-6 sentences.",
            "Use inline citations [n] when supported by the context.",
            "If a SOURCES section and Query ID are present in the context, keep them in the final output.",
            "Do not repeat full chunk contents."
        ].join(" ")),
        vscode.LanguageModelChatMessage.User(`User question: ${request.prompt}\n\nRetrieved context:\n${retrievedContext}`)
    ];
    const response = await request.model.sendRequest(messages, {}, token);
    let text = "";
    for await (const fragment of response.text) {
        text += fragment;
    }
    return text.trim();
}
function activate(context) {
    void ensureFirstRunMcpSync(context);
    const handler = async (request, _chatContext, stream, token) => {
        const { mcpUrl, toolName, githubUserHeader, profile, topK, includeSources, summarizeWithCopilot: useCopilotSummary } = getConfig();
        const controller = new AbortController();
        const cancellationSubscription = token.onCancellationRequested(() => controller.abort());
        try {
            const text = await callMcpTool(mcpUrl, toolName, {
                query: request.prompt,
                profile,
                top_k: topK
            }, includeSources, githubUserHeader, controller.signal);
            if (useCopilotSummary) {
                try {
                    const summarized = await summarizeResponseWithCopilot(request, text, token);
                    stream.markdown(summarized || formatToolOutput(text));
                }
                catch {
                    // Fall back to raw retrieved output when model summarization is unavailable.
                    stream.markdown(formatToolOutput(text));
                }
            }
            else {
                stream.markdown(formatToolOutput(text));
            }
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            stream.markdown(`@knowledge MCP call failed: ${message}`);
        }
        finally {
            cancellationSubscription.dispose();
        }
        return {
            metadata: {
                participant: "knowledge"
            }
        };
    };
    const participant = vscode.chat.createChatParticipant("kbsearch-extension.knowledge", handler);
    participant.iconPath = vscode.Uri.joinPath(context.extensionUri, "media", "knowledge.svg");
    context.subscriptions.push(participant);
}
function deactivate() {
    // No-op
}
//# sourceMappingURL=extension.js.map