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

Queries must contain 1–4000 characters of nonwhitespace text; `top_k` is 1–100.
Bodies are limited to 8 MiB, including streamed requests (413 when exceeded). Optional `filters` match metadata values (string, number, boolean, or string-list membership). Filter keys and values are SQL parameters. Values retain JSON types: a numeric
filter does not match a stored numeric string, and incompatible metadata types
are nonmatches. Up to 20 filters are accepted, with 1–200-character keys; string
values are limited to 1000 characters and lists to 1–100 strings. Nested objects,
null and nonfinite numbers are rejected with 422. Tracking saves query, metrics, and chunk references atomically. If tracking fails, retrieval succeeds with `feedback_available: false`; feedback is unavailable for that query.

`GET /health` checks database access, required schema, environment/schema dimensions; it returns 503 on failure. Startup also loads and validates the default embedding model and optional reranker. `/metrics`, `/docs`, and `/openapi.json` remain available. Feedback uses `POST /feedback` with `query_id`, `score` (0–10), and optional `comment`.

### Optional reranking

Set the following in `.env`, then restart the API:

```dotenv
ENABLE_RERANKING=true
RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RERANK_TOP_K=10
```

Hybrid branches consider a bounded 50–200 candidates each, scaling with the
requested retrieval/reranking count. Publication dates drive a modest recency
boost that decays smoothly toward neutral; missing/invalid publication dates
fall back to modification dates. Undated and future-dated records are neutral.

Retrieval selects up to `max(top_k, RERANK_TOP_K)` candidates, the local cross-encoder scores query/document pairs, and the service returns the best `top_k` chunks. Models are reused and inference runs outside the async event loop. Tokenizer
preparation shares model admission, and indexing releases admission between
batches so interactive queries can proceed. `INFERENCE_QUEUE_TIMEOUT_SECONDS`
(default 30, maximum 300) and `INFERENCE_MAX_PENDING` (default 16, maximum 256)
bound waiting per model. A full/expired queue returns 503 with `Retry-After: 30`;
cancellation stops subsequent batches without allowing overlapping model calls. Reranking errors fail the request rather than silently returning a different ranking.

All embedding, chunking, retrieval, and reranking configuration comes from `.env` or environment variables (environment variables take precedence). For example, `CHUNK_SIZE=200` controls ingestion chunks and `RETRIEVAL_TOP_K=5` controls the default number of returned chunks. A request's `top_k` overrides that retrieval default; metadata `filters` remain request-specific. Restart the API after changing configuration. The template lists only active settings: index tuning belongs in `db/init.sql`, crawling depth/page limits are request fields, and hybrid search uses fixed reciprocal rank fusion weights. `RELEVANCE_THRESHOLD` applies only to pure vector search. Changing the embedding model requires re-ingesting existing documents, even if the vector dimension stays the same.

### API compatibility

`/query` always retrieves; answer generation has been removed. Existing retrieval clients may continue sending `retrieve_only: true`. Omission is also supported; `false` returns HTTP 422. Clients formerly reading `answer` must synthesize from `chunks` themselves. Metrics describe retrieval and reranking only. `/profiles`, `/report`, profile selection, and stored configuration snapshots have been removed. Older request payloads containing `profile` are ignored; the service always uses its environment configuration.

## Ingestion limits and errors

`POST /ingest` requires exactly one of `url` or `content`. Document content is
limited to 2,000,000 characters, titles to 2000, URLs to 4000 and metadata to
32 KiB of JSON. Document upserts accept at most 2000 sections, whose combined
text must also fit 2,000,000 characters; section references are at most 500
characters. Preparation rejects documents producing over 10,000 chunks. Word
chunks retain paragraphs and short endings, then split further to fit the loaded
embedding tokenizer's limit, including special tokens.

The optional URL crawler accepts public HTTP(S) URLs on ports 80/443 without
credentials. It validates actual DNS connection addresses and every redirect,
allows at most five redirects, and downloads at most 5 MiB of decompressed HTML
or plain text per page within ten seconds. Private/special-use destinations,
unsupported types and local files are rejected. Internal material can still be
supplied as direct content or document upserts. The document API never fetches
its URL field.

Recursive crawls have a 120-second download budget; `max_pages` bounds attempted
pages, including failed and nonmatching pages. Depth 1 means only the starting
page, depth 2 includes its immediate links. Excluded pages are never fetched.
Links and content are extracted from one response per page; failed pages and
work beyond the budget may be skipped. No retry records are created.

Validation failures return readable 422 errors without echoing submitted text;
oversized bodies return 413. Unavailable page downloads return 502 with a retry
suggestion.
Unknown/expired feedback query IDs return 404. Unexpected failures return a
generic 500 and are logged on the server without exposing exception text to
clients. Request cancellation does not kill an already executing model thread;
its admission remains held until that thread finishes.
