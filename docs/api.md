# HTTP and MCP contracts

[Quickstart](../README.md)

Both adapters use the same service. HTTP is an optional local/trusted-network
interface for applications such as Lalaland; there is no authentication layer or
cross-origin browser API. Native HTTP defaults to `127.0.0.1:8000`; Compose and
managed forwards use port 8082. Keep container ports on trusted interfaces.

## Retrieval

`POST /query` accepts `query` (1–4000 nonblank characters), optional `top_k`
(1–100, default configured `RETRIEVAL_TOP_K=5`), and optional `filters`.
The legacy `retrieve_only` field still accepts only `true`.
MCP `search_knowledge_base(query, top_k=5, filters=null)` uses the same contract.

Filters match typed scalar JSON values or a list of 1–100 strings. There are at
most 20 keys, each at most 200 characters; string values are at most 1000
characters. Collection/document filters are logical scopes, not authorization.

Responses contain `chunks` and transient `metrics` (`latency_ms`,
`latency_rerank_ms`, `chunks_retrieved`). No query text/results are stored in
history, and no `query_id` or `feedback_available` is returned.
Each chunk preserves `chunk_id`, `[n]` citation, content, title, URL, score,
optional rerank score, section reference/index, collection/document ID, revision,
content type, publication date, and modification date. URL-free text is retained.
An empty match returns an empty list, not generated prose.

Hybrid retrieval uses equal-weight reciprocal rank fusion of PostgreSQL keyword
and vector candidates, followed by publication-recency boosting. The relevance
threshold applies to vector-only mode, not the smaller RRF scores. Optional
reranking sorts the candidate shortlist using a local cross-encoder; rerank
scores are not probabilities. Feedback/popularity boosts are removed.

## Documents and readiness

`PUT /documents/{collection}/{document_id}` and MCP `upsert_document` accept:

- `content`: nonblank supplied text, at most 2,000,000 characters.
- `title`, optional `url` provenance, `published_at`, and `content_type`.
- `metadata`: finite JSON, at most 32768 UTF-8 bytes.
- Optional `sections`: up to 2000 `{ref, text}` entries totaling at most
  2,000,000 text characters; sections replace content for chunking.

Collection/document IDs must have no surrounding whitespace and contain 1–200 /
1–500 characters respectively. The complete JSON body is limited to 8 MiB over
HTTP. Documents producing more than 10,000 chunks are rejected before embedding.
The URL field records provenance and is never fetched.

Upserts return collection/document ID, revision, chunk and embedding counts, and
`changed`. Repeated identical delivery is a no-op; metadata-only changes preserve
vectors and chunk IDs. DELETE and MCP `delete_document` are idempotent. See
[document guarantees](documents.md) for locking and identity requirements.

`GET /health` and `lassy_status` check database/schema and index identity. Startup
also validates prepared embedding/reranking models. HTTP invalid input is 422,
busy inference/document operations return 503 with `Retry-After: 30`, and health
failure is 503. Unexpected request failures use sanitized errors. MCP returns
protocol tool errors with `invalid`, `busy`, or `unavailable` messages.

Removed routes: `/ingest`, `/feedback`, `/metrics`. MCP has no feedback tool or
`include_sources` switch; callers format the structured source fields themselves.
