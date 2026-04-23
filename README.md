# KB Search Mention

Minimal VS Code extension that adds an `@knowledge` chat participant.

## Run locally

1. Install dependencies:

   ```bash
   npm install
   ```

2. Compile:

   ```bash
   npm run compile
   ```

3. Press `F5` in VS Code to launch an Extension Development Host.

4. Open Chat in the dev host and type `@knowledge` followed by your question.

## Notes

- `@knowledge` supports two backends:
   - MCP mode: calls a configurable MCP endpoint and invokes an MCP tool.
   - Direct mode: calls the RAG `/query` endpoint directly (`retrieve_only=true`), then formats chunks/sources similarly to the MCP tool output.

## Configuration

Configure in VS Code settings:

- `kbsearch.connectionMode` (default: `direct`) - `mcp` or `direct`
- `kbsearch.mcpUrl` (default: `http://localhost:8080/mcp`)
- `kbsearch.toolName` (default: `search_knowledge_base`)
- `kbsearch.ragUrl` (default: `http://localhost:8000/query`) - used in `direct` mode
- `kbsearch.includeSources` (default: `true`)

## Direct Mode Behavior

In `direct` mode, the extension:

- Calls `kbsearch.ragUrl` with:
   - `query`
   - `retrieve_only=true`
- Preserves `Query ID` and optional `SOURCES` in the generated context for summarization

## MCP Mode Behavior

In `mcp` mode, the extension keeps the existing JSON-RPC flow:

- `initialize`
- `notifications/initialized`
- `tools/call` for `kbsearch.toolName`
