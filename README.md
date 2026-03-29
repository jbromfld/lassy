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

- This extension does not call Copilot language models.
- `@knowledge` calls a configurable MCP endpoint and invokes an MCP tool.

## Configuration

Configure in VS Code settings:

- `kbsearch.mcpUrl` (default: `http://localhost:8080/mcp`)
- `kbsearch.mcpServerName` (default: `cicd-mcp`)
- `kbsearch.syncMcpConfigOnInstall` (default: `true`)
- `kbsearch.toolName` (default: `search_knowledge_base`)
- `kbsearch.githubUserHeader` (default: empty)
- `kbsearch.profile` (default: `default`)
- `kbsearch.topK` (default: `5`)
- `kbsearch.includeSources` (default: `true`)
- `kbsearch.summarizeWithCopilot` (default: `true`)

## First-run behavior

On first activation (when `kbsearch.syncMcpConfigOnInstall` is enabled), the extension:

- Reads user `mcp.json`
- If `kbsearch.mcpUrl` is not explicitly set, uses the URL from `servers[kbsearch.mcpServerName]`
- Creates `servers[kbsearch.mcpServerName]` in `mcp.json` if missing

Deprecated settings:

- `kbsearch.ragUrl`
- `kbsearch.minScore`
