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

The schema contains documents, embeddings, queries, metrics, and feedback. It creates no database or role. If the runtime role differs from the owner, grant it schema usage and access to tables, sequences, and functions.

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
mkdir -p .cache/huggingface
docker compose up -d --build lassy
docker compose logs -f lassy
docker compose down
```

The Docker build context includes only runtime source and dependency/build files; local virtual environments, `.env`, Git history, tests and downloaded models are excluded. The Dockerfile installs binary wheels directly with a BuildKit pip download cache, skips bytecode compilation, and installs no compiler toolchain. Application-only edits reuse the dependency layer. Builds require compatible wheels for the target Python/Linux architecture and fail instead of compiling packages from source.

Compose runs only the API. `API_PORT` controls its published host port (8082 when unset); the container listens on 8000. Model downloads persist in a local bind mount described below. The API has no authentication; bind/publish it to your intended network.

## Persistent local model cache

Compose mounts `./.cache/huggingface` on the Docker host at `/app/.cache/huggingface` in the container and sets `HF_HOME` to that container path. Both the PyTorch embedding model and the optional reranker use this cache. It survives container replacement, image rebuilds, and `docker compose down`, and is excluded from Git and the Docker build context. Rebuilding the image does not update cached models. With a remote Docker daemon, the source directory must exist on that daemon's host.

### Mount and prepare

Run from the repository root after configuring `.env`:

```bash
mkdir -p .cache/huggingface
docker compose build lassy
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 lassy python -m core.model_cache
```

The preparation command downloads and validates the configured embedding model and, when `ENABLE_RERANKING=true`, the reranker. It uses the same model loaders as the API without opening a database connection. It still reads the normal `.env` settings, including the required PostgreSQL credential fields.

To store models elsewhere, set an absolute host path in `.env` and create that directory before running the commands above:

```dotenv
MODEL_CACHE_DIR=/absolute/path/to/lassy-model-cache
```

Relative paths are resolved from the Compose file's directory. The mount must be writable by the container's `appuser` (UID/GID 1000). On Linux, if necessary, give that user ownership of the dedicated cache directory:

```bash
sudo chown -R 1000:1000 .cache/huggingface
```

Substitute your chosen directory when using a custom `MODEL_CACHE_DIR`. For `docker run`, the equivalent mount and cache setting are:

```bash
docker run --rm --env-file .env \
  --mount "type=bind,source=$(pwd)/.cache/huggingface,target=/app/.cache/huggingface" \
  -e HF_HOME=/app/.cache/huggingface -e HF_HUB_OFFLINE=0 \
  lassy-lassy python -m core.model_cache
```

Use your image tag if it differs from Compose's default `lassy-lassy`.

### Start using cached models

After preparation succeeds, set this in `.env`:

```dotenv
HF_HUB_OFFLINE=1
```

Then verify the cache and start the API:

```bash
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=1 lassy python -m core.model_cache
docker compose up -d --force-recreate lassy
```

Offline mode skips Hugging Face network checks and fails if required model files are missing. It does not skip model loading or validation inference. Leave `HF_HUB_OFFLINE=0` for automatic first-run downloads. See the [Hugging Face cache environment documentation](https://huggingface.co/docs/huggingface_hub/v0.26.5/package_reference/environment_variables).

### Update cached models

Stop the API before modifying its cache, run preparation with downloads enabled, verify offline loading, then recreate the API:

```bash
docker compose stop lassy
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 lassy python -m core.model_cache
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=1 lassy python -m core.model_cache
docker compose up -d --force-recreate lassy
```

The online command checks the configured model repositories for updates and downloads missing files. Previously cached revisions can remain on disk. Changing the embedding model or using updated embedding weights requires re-ingesting documents; a different output dimension also requires a matching database schema. Updating only the reranker does not require re-embedding documents.

### Purge downloaded models

For the default mount, stop the API and remove only the Hub model cache:

```bash
docker compose stop lassy
rm -rf .cache/huggingface/hub
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 lassy python -m core.model_cache
docker compose up -d --force-recreate lassy
```

For a custom mount, remove `hub` inside your `MODEL_CACHE_DIR` instead. Purging removes model downloads and cached revisions, preserves any Hugging Face token outside `hub`, and leaves PostgreSQL documents/embeddings intact. Download again before starting in offline mode. Purging is not needed for routine updates.

### Migrate the previous Docker volume

Before recreating an existing container that still uses the old `embeddings_cache` volume, copy its downloaded files into the new local directory:

```bash
docker compose stop lassy
mkdir -p .cache/huggingface
docker cp lassy:/app/.cache/huggingface/. ./.cache/huggingface/
```

Use your custom destination if applicable, ensure UID 1000 can write to it, then build the new image, verify the cache offline, and recreate the API as above. The old named volume remains available until explicitly removed; the new Compose configuration no longer mounts it.

### Run without Docker

Use the same local cache with the activated Python environment. Export these variables in the shell; Hugging Face does not read the application's `.env` file itself:

```bash
export HF_HOME="$PWD/.cache/huggingface"
mkdir -p "$HF_HOME"
HF_HUB_OFFLINE=0 python -m core.model_cache
export HF_HUB_OFFLINE=1
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

To refresh, stop the API and repeat preparation with `HF_HUB_OFFLINE=0`. To purge, remove `"$HF_HOME/hub"` while the API is stopped and prepare again. Sharing the cache between native Python and Docker also requires compatible host/container file permissions.

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

## Shared document ingestion

Lassy owns the single text ingestion/retrieval pipeline for all clients: chunking,
embedding, PostgreSQL keyword + cosine search, reciprocal rank fusion, boosts and
optional reranking. Clients own source records and answer generation. Both legacy
`POST /ingest` (including crawled pages) and document upserts use the same pipeline.

For an existing retrieval-only installation, apply the **additive** migration before
restarting the API; do not reset your database:

```bash
psql -h localhost -U YOUR_USER -d YOUR_DATABASE -v ON_ERROR_STOP=1 -1 -f db/002_documents.sql
```

New installations use `db/init.sql`, which includes the document table. Readiness
requires the new table. Existing chunks remain searchable. Legacy chunks without
collection/document metadata remain unscoped: a collection filter excludes them.
Re-ingesting legacy material creates scoped documents; old unscoped chunks require
explicit cleanup if you want to avoid duplicates in unfiltered searches.

```bash
curl -X PUT http://localhost:8082/documents/lalaland-rss/123 \
  -H 'Content-Type: application/json' \
  -d '{"content":"A short saved VLM summary.","title":"VLM paper","url":"https://example.org/paper","content_type":"rss_summary","published_at":"2026-09-12T10:00:00Z"}'
curl http://localhost:8082/query -H 'Content-Type: application/json' \
  -d '{"query":"vision language models","filters":{"collection":["lalaland-rss","lalaland-corpus"]},"top_k":10}'
curl -X DELETE http://localhost:8082/documents/lalaland-rss/123
```

`PUT /documents/{collection}/{document_id}` accepts `content`, optional `title`,
`url`, `published_at`, `content_type`, `metadata`, and `sections` (each with `ref`
and `text`). When supplied, sections are the authoritative text for chunking;
provide every section you want indexed. Each chunk retains its section reference.
Clients send original text, not embeddings or retrieval chunks. Collection and
ID identify a document; they are logical scopes, not authentication boundaries.

Upserts publish all replacement chunks atomically after successful embedding.
Identical requests skip embedding when their document/configuration revision is
already indexed. Changed text, metadata, chunking configuration, model name or
dimension changes that revision. Identical text in different documents retains
separate provenance. Deletes are idempotent and serialized with publication.
Query chunks include `collection`, `document_id`, `revision`, `content_type`,
`published_at` and the source section reference in `section`.

Embedding weight changes under the same model name require bumping
`pipeline_version` in `core/pipeline/documents.py` and backfilling every source.
Do not mix different embedding models in one searchable index; stop serving
queries during a full model migration or build a replacement index. The document
API does not fetch supplied URLs; each source application retains its fetching policy.

### Evaluate retrieval centrally

Run the same HTTP evaluation against all source types, using manually judged
expected document IDs. Create a JSONL dataset such as:

```json
{"query":"vision language model deployment","filters":{"collection":"lalaland-corpus"},"expected":[{"collection":"lalaland-corpus","document_id":"123"}]}
```

Replace the example with real judgments, then run:

```bash
python evaluate.py judgments.jsonl --url http://localhost:8082 --top-k 10
```

The report includes document recall@k, reciprocal rank, and per-query retrieval
IDs for inspection. Repeated chunks count as one document. Include RSS, corpus,
and web-document judgments; compare per-query results when tuning the one shared
pipeline. This measures retrieval, not generated-answer quality or exhaustive
coverage of every article about a topic.

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

The API speaks HTTP. MCP mode continues to use the separate `local-mcp-server` adapter; its retrieval calls remain compatible. Other clients can use the same `/query` contract and send chunks to whichever generator they choose.

## Tests

```bash
python -m unittest discover -s tests -v
# Creates/drops uniquely named disposable databases; requires CREATEDB/extension privileges:
RAG_TEST_POSTGRES=1 POSTGRES_HOST=127.0.0.1 DEBUG=false python -m unittest discover -s tests -v
# Also verify live API + CLI ingestion/search/feedback with a cached embedding model:
RAG_TEST_POSTGRES=1 RAG_TEST_MODEL=1 POSTGRES_HOST=127.0.0.1 DEBUG=false python -m unittest discover -s tests -v
```

Run `npm test` in the extension repository for compilation and direct-mode tests. Complete an F5 `@knowledge` check to verify the installed VS Code/chat model environment.
