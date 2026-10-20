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
