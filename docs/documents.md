# Shared documents and evaluation

[← Quickstart](../README.md)

Run commands from the repository root unless stated otherwise.

## Shared document ingestion

Lassy owns the single text ingestion/retrieval pipeline for all clients: chunking,
embedding, PostgreSQL keyword + cosine search, reciprocal rank fusion, boosts and
optional reranking. Clients own source records and answer generation. Both legacy
`POST /ingest` (including crawled pages) and document upserts use the same pipeline.

For an existing retrieval-only installation, apply the **additive** migration before
restarting the API; do not reset your database:

```bash
psql -h localhost -U YOUR_USER -d YOUR_DATABASE -v ON_ERROR_STOP=1 -1 \
  -f db/002_documents.sql -f db/003_practical.sql
```

New installations use `db/init.sql`, which includes both schema additions.
The new `003_practical.sql` adds an embedding fingerprint and removes retained chunk
text from old query-history snapshots; document text, vectors, metrics and feedback
remain intact. Apply the SQL before restarting the new API. Readiness
requires the new table and fingerprint column. Existing chunks remain searchable. Legacy chunks without
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
dimension changes the document revision. Metadata-only changes retain existing
vectors, chunk IDs, section references, retrieval counts and feedback. Embedding
fingerprints include the tokenizer limit and normalization options. Crawl
`scraped_at` observations do not invalidate an otherwise unchanged document. Identical text in different documents retains
separate provenance. Deletes are idempotent. Preparation, replacement and deletion for each document
are serialized within the supported single API process, with a 30-second lock
wait limit (HTTP 503 on contention). No database connection is held during model
work. Keep one API process and one replica when accepting document writes;
transaction locks alone do not order preprocessing across multiple processes.
Avoid overlapping writer replicas during rolling updates (pause source delivery
or use a recreate deployment strategy).
Query chunks include `collection`, `document_id`, `revision`, `content_type`,
`published_at` and the source section reference in `section`.

This update changes chunk boundaries and token-limit handling. Existing vectors
remain searchable, but backfill source records to benefit from the fixes. For
Lalaland, run its existing `python -m backend.services.knowledge backfill`, then
allow scheduled delivery (or run its sync command). The first delivery under the
new pipeline re-embeds each document once; later date/title changes reuse vectors.
Other clients should resend their original documents. Do not reset the index.

Embedding weight changes under the same model name require bumping
`PIPELINE_VERSION` in `core/pipeline/documents.py` and backfilling every source.
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

## Query-history retention

New query-history records store chunk identifiers, citations, scores and document
provenance, without copying source excerpts. Feedback still uses the recorded
chunk IDs. Deleting an indexed document removes its searchable text; historical
query questions and feedback remain.

There is no automatic pruning schedule. From the repository root, using the API's
database settings, preview and remove old **unrated** queries in bounded batches:

```bash
python -m core.metrics.prune --days 90 --limit 1000 --dry-run
python -m core.metrics.prune --days 90 --limit 1000
# Compose equivalent:
docker compose exec lassy python -m core.metrics.prune --days 90 --dry-run
```

Each invocation removes at most `limit` eligible queries and their metrics. Repeat
if needed. Rated queries and their metrics/feedback are preserved indefinitely,
so pruning does not change feedback-based ranking. Old query IDs removed by
pruning return 404 when feedback is submitted. No source documents are deleted.
