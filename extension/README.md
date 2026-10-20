# KB Search Mention

Minimal VS Code extension that adds an `@knowledge` chat participant.

## Build a VSIX for testing

Run from the `extension/` directory:

```bash
npm run build
```

Or run `./build.sh` directly, from any directory. The script installs dependencies
if the local compiler or packager is missing, runs the tests, and packages
`build/kbsearch-extension-<version>.vsix`. Install it explicitly with `./install.sh` after reviewing the build. After dependency changes, run `npm ci`
before rebuilding. Node.js 20.20.2 and npm are required for packaging. The VS Code `code` command is required only for installation.

Reload the VS Code window after installing or replacing the extension.

Start the retrieval service separately and ingest some content. Configure VS Code
to connect directly (these are already the defaults):

```json
{
  "kbsearch.connectionMode": "direct",
  "kbsearch.ragUrl": "http://localhost:8000/query"
}
```

Then open Chat and ask `@knowledge` about the ingested content. A VS Code chat
model must be available to generate answers; if generation fails, the extension
shows the retrieved context. For a remote Extension Host, set `kbsearch.ragUrl`
to an address reachable from that host. Start the retrieval service separately.

## Run locally

1. Install dependencies:

   ```bash
   npm install
   ```

2. Compile:

   ```bash
   npm run compile
   ```

3. Open the Lassy repository root (or this `extension/` folder) in VS Code and
   press `F5` to launch an Extension Development Host.

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
- Validates the retrieval response; malformed payloads produce an error
- Supplies citation labels with each chunk to VS Code’s selected model
- Appends `Query ID`, feedback availability, and optional `SOURCES` independently of model output
- Falls back to retrieved context if generation fails; cancellation suppresses output
- Does not launch the service; run the RAG API separately with initialized external PostgreSQL/pgvector and the local embedding model
- Requires no server-side Copilot key; VS Code provides answer generation

Use `npm test` to compile and run direct-mode regression tests. For an end-to-end check, start the API, ingest a document, launch F5, and ask `@knowledge` about that document. Check that source labels match the answer, disable `kbsearch.includeSources` to hide the source list, and verify cancellation. When using a remote Extension Host, `localhost` refers to that host.

## MCP Mode Behavior

In `mcp` mode, the extension keeps the existing JSON-RPC flow:

- `initialize`
- `notifications/initialized`
- `tools/call` for `kbsearch.toolName`

## Bazel pilot

From the Lassy repository root with Bazel 9.2.0, run
`bazel test //extension:direct_test` and `bazel build //extension:vsix`.
The output is `bazel-bin/extension/kbsearch-extension.vsix`; installation remains explicit.
Bazel uses a declared Node 20.20.2 toolchain and a pnpm lock generated from
`package-lock.json`. After npm dependency changes, regenerate it with pnpm 10.22.0
`pnpm import`. Shared setup is documented in
`../../local-agent-environment/README.md`.
