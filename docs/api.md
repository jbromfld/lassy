# API and retrieval reference

[← Quickstart](../README.md)

Run commands from the repository root unless stated otherwise.

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
