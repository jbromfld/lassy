# RAG Retrieval Service

A standalone service for document ingestion, hybrid retrieval, and optional local reranking. It uses external PostgreSQL with pgvector and local embedding models. Clients receive ranked chunks with citations and decide how to generate answers: a local LLM, VS Code's selected chat model, or any other client. The service contains no LLM generation provider and requires no LLM API key.

## Setup

Use Python 3.11 and PostgreSQL 15+ with pgvector installed on the server. The default schema and embedding settings use 768-dimensional `sentence-transformers/all-mpnet-base-v2` embeddings. Model files must be downloaded on first use or already cached.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
```

Set `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in `.env`. Native API: use `localhost`. API container connecting to the host: use `host.docker.internal`. Set `DEBUG=false`; environment variables override `.env`, so unset any unrelated nonboolean shell `DEBUG` variable.

Have your administrator create the database and grant the initializing role permission to enable pgvector. Initialize once in an empty database:

```bash
psql -h localhost -p 5432 -U YOUR_USER -d YOUR_DATABASE -v ON_ERROR_STOP=1 -1 -f db/init.sql
```

The schema contains only embeddings, queries, metrics, and feedback. It creates no database or role. If the runtime role differs from the owner, grant it schema usage and access to tables, sequences, and functions.

### Replace an older service schema

This version deliberately removes server-side generation and its schema. To replace an older schema, **delete the old service data and recreate it**, then re-ingest your documents:

```bash
psql -h localhost -p 5432 -U YOUR_USER -d YOUR_DATABASE -v ON_ERROR_STOP=1 -1 \
  -f db/cleanup.sql -f db/init.sql
```

Startup never resets or migrates a database automatically. There are no database configuration profiles or seed records. A different embedding dimension requires a matching schema and environment settings, followed by re-ingestion.

## Run

From the repository root:

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8000
# Alternatively, reads API_PORT and DEBUG; binds to 0.0.0.0:
python -m api.main
```

Or, after configuring `.env` and the external database:

```bash
docker compose up -d --build rag-service
docker compose logs -f rag-service
docker compose down
```

The Docker build context includes only runtime source and dependency/build files; local virtual environments, `.env`, Git history, tests and downloaded models are excluded. The Dockerfile installs binary wheels directly with a BuildKit pip download cache, skips bytecode compilation, and installs no compiler toolchain. Application-only edits reuse the dependency layer. Builds require compatible wheels for the target Python/Linux architecture and fail instead of compiling packages from source.

Compose runs only the API. `API_PORT` controls its published host port; the container listens on 8000. Model downloads persist in the `embeddings_cache` volume through `HF_HOME=/app/.cache/huggingface`. The API has no authentication; bind/publish it to your intended network.

## Ingest and search

```bash
export RAG_SERVICE_URL=http://localhost:8000  # optional CLI override
python cli.py health
python cli.py ingest https://docs.example.com
python cli.py ringest https://docs.example.com 2 50
python cli.py query "How does configuration work?"
python cli.py feedback QUERY_UUID 9 "Helpful results"
```

`POST /ingest` accepts `url` or direct `content`, optional `title` and `metadata`. Recursive URL ingestion also supports `depth`, `max_pages`, `url_patterns`, and `exclude_patterns`.

```bash
curl http://localhost:8000/ingest -H 'Content-Type: application/json' \
  -d '{"title":"Local guide","content":"Your document goes here; provide enough text to produce chunks."}'
curl http://localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"query":"How does configuration work?","top_k":5}'
```

`POST /query` returns `query_id`, `chunks`, `feedback_available`, and `metrics`. Each ranked chunk has `chunk_id`, `citation` (`[1]`, `[2]`, ...), `content`, `title`, `url` (possibly empty), `score` (boosted retrieval score), `rerank_score` (local cross-encoder score, or null), `section`, and `last_modified`. Multiple chunks from the same document remain separate. Empty results return `chunks: []`.

Queries must contain nonwhitespace text; `top_k` must be positive. Optional `filters` match metadata values (string, number, boolean, or string-list membership). Filter keys and values are SQL parameters. Tracking saves query, metrics, and chunk references atomically. If tracking fails, retrieval succeeds with `feedback_available: false`; feedback is unavailable for that query.

`GET /health` checks database access, required schema, environment/schema dimensions; it returns 503 on failure. Startup also loads and validates the default embedding model and optional reranker. `/metrics`, `/docs`, and `/openapi.json` remain available. Feedback uses `POST /feedback` with `query_id`, `score` (0–10), and optional `comment`.

### Optional reranking

Set the following in `.env`, then restart the API:

```dotenv
ENABLE_RERANKING=true
RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RERANK_TOP_K=10
```

Retrieval selects up to `max(top_k, RERANK_TOP_K)` candidates, the local cross-encoder scores query/document pairs, and the service returns the best `top_k` chunks. Models are reused and inference runs outside the async event loop. Reranking errors fail the request rather than silently returning a different ranking.

All embedding, chunking, retrieval, and reranking configuration comes from `.env` or environment variables (environment variables take precedence). For example, `CHUNK_SIZE=200` controls ingestion chunks and `RETRIEVAL_TOP_K=5` controls the default number of returned chunks. A request's `top_k` overrides that retrieval default; metadata `filters` remain request-specific. Restart the API after changing configuration. The template lists only active settings: index tuning belongs in `db/init.sql`, crawling depth/page limits are request fields, and hybrid search uses fixed reciprocal rank fusion weights. `RELEVANCE_THRESHOLD` applies only to pure vector search. Changing the embedding model requires re-ingesting existing documents, even if the vector dimension stays the same.

### API compatibility

`/query` always retrieves; answer generation has been removed. Existing retrieval clients may continue sending `retrieve_only: true`. Omission is also supported; `false` returns HTTP 422. Clients formerly reading `answer` must synthesize from `chunks` themselves. Metrics describe retrieval and reranking only. `/profiles`, `/report`, profile selection, and stored configuration snapshots have been removed. Older request payloads containing `profile` are ignored; the service always uses its environment configuration.

## VS Code and MCP clients

Compile `kbsearch-extension` and launch its Extension Development Host with F5. Default settings are:

```json
{
  "kbsearch.connectionMode": "direct",
  "kbsearch.ragUrl": "http://localhost:8000/query",
  "kbsearch.includeSources": true
}
```

Ask `@knowledge` a question. The extension supplies retrieved chunks to VS Code's selected chat model, then appends sources and query details itself. Model failure falls back to retrieved context. The extension does not start the service. In a remote Extension Host, `localhost` refers to that host.

The API speaks HTTP. MCP mode continues to use the separate `kbsearch-mcp-server` adapter; its retrieval calls remain compatible. Other clients can use the same `/query` contract and send chunks to whichever generator they choose.

## Tests

```bash
python -m unittest discover -s tests -v
# Creates/drops uniquely named disposable databases; requires CREATEDB/extension privileges:
RAG_TEST_POSTGRES=1 POSTGRES_HOST=127.0.0.1 DEBUG=false python -m unittest discover -s tests -v
# Also verify live API + CLI ingestion/search/feedback with a cached embedding model:
RAG_TEST_POSTGRES=1 RAG_TEST_MODEL=1 POSTGRES_HOST=127.0.0.1 DEBUG=false python -m unittest discover -s tests -v
```

Run `npm test` in the extension repository for compilation and direct-mode tests. Complete an F5 `@knowledge` check to verify the installed VS Code/chat model environment.
