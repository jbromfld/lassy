# RAG MCP Service

A RAG (Retrieval-Augmented Generation) ingestion and retrieval service for GitHub Copilot.

The service handles **ingestion** (scraping, chunking, embedding, storing in pgvector), **retrieval** (hybrid vector + keyword search), and **generation** through the Copilot API. A retrieve-only mode still exists when a caller wants raw chunks back.

---

## How It Works

```
You (in Copilot)
  └── asks a question
       └── .mcp.json routes to kbsearch-mcp-server
            └── MCP server calls POST /query?retrieve_only=true
                 └── RAG service returns ranked chunks with citations
                      └── Copilot synthesizes the answer
```

The `default` profile uses the Copilot provider for full RAG responses.
Profiles are now intended to capture retrieval and summarization presets: embedding choice, chunking, retrieval settings, and LLM hyperparameters.

---

## Quick Start

```bash
# 1. Start the RAG service
docker-compose up -d

# 2. Copy and configure environment
cp .env.template .env

# 3. Ingest a knowledge source
python cli.py ringest https://your-docs-url.com 2 50

# 4. Query
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I configure X?", "profile": "default"}'
```

---

## Copilot Integration

`.mcp.json` can still wire Copilot to a local MCP server, but this service no longer depends on an internal `mcp` LLM provider. Callers that want raw retrieval results can keep using `retrieve_only: true`.

```json
{
  "servers": {
    "local-kbsearch-server": {
      "command": "/path/to/kbsearch-mcp-server/.venv/bin/python",
      "args": ["/path/to/kbsearch-mcp-server/server.py"],
      "type": "stdio"
    }
  }
}
```

---

## API Endpoints

### `POST /ingest`
Scrape and embed a URL into the knowledge base.

```json
{
  "url": "https://docs.example.com",
  "depth": 2,
  "max_pages": 50,
  "profile": "default"
}
```

### `POST /query`
Run retrieval and generate a Copilot-backed answer. Use `retrieve_only: true` only when the caller wants raw chunks instead of generation.

```json
{
  "query": "How do I configure X?",
  "top_k": 5,
  "profile": "default"
}
```

Returns an answer with citations by default, or chunks with citations and relevance scores when `retrieve_only` is set.

### `POST /feedback`
Submit feedback (0–10) on a query response to improve future retrieval.

### `GET /metrics`
View retrieval latency, chunk scores, and feedback statistics.

---

## Profiles

| Profile | Description |
|---|---|
| `default` | Copilot API handles full RAG + generation |
| `copilot-gpt4o` | Explicit Copilot GPT-4o profile |

Use profiles when you want to vary embedding, chunking, retrieval, or summary behavior. Global service settings remain environment-level configuration.

---

## CLI

```bash
python cli.py health                          # Check service health
python cli.py ingest <url>                    # Ingest a single page
python cli.py ringest <url> <depth> <pages>   # Recursive ingestion
python cli.py query "<question>"              # Query the knowledge base
python cli.py feedback <query_id> <0-10>      # Submit feedback
python cli.py metrics                         # View metrics
python cli.py profiles                        # List profiles
```

---

## Infrastructure

```bash
docker-compose up -d    # Start postgres + api
docker-compose down     # Stop
docker-compose logs -f api  # View API logs
```

Services:
- `postgres` — PostgreSQL 16 with pgvector (port 5434)
- `api` — FastAPI service (port 8000)
