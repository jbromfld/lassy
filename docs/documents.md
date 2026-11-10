# Document storage, upgrades, and model identity

[Quickstart](../README.md)

Documents are keyed by collection/document ID. Text, section references, metadata,
and publication dates determine the document revision. Chunking, model identity,
and token limits determine the embedding revision. Observation-only `scraped_at`
metadata is excluded, preserving existing ingestion fingerprints.

Repeated deliveries reuse the stored result; metadata-only updates retain vectors
and chunk IDs. Text changes prepare all chunks/embeddings before a short atomic
replacement transaction. PostgreSQL advisory locks serialize the full read,
preparation, publication, and deletion sequence across processes. No transaction
is held during model inference; the operation holds one pool connection and reuses
it, so a size-one pool also works. Lock waits default to 30 seconds. Cancellation
unlocks the session or closes its connection before returning it to the pool.

## Fresh installations

Apply `db/init.sql` only to an empty database. The runtime needs access to
`documents`, `embeddings`, and `lassy_index_identity`. A fresh empty index binds
its model/chunking identity at first startup. All runtime processes must use that
same identity. There are no query, metrics, or feedback tables in the fresh schema.

## Upgrade existing installations

Stop old writers during adoption and back up the database/model configuration.
Existing vectors must match the configured pinned model snapshot, embedding
dimension, normalization, token limit, and chunking configuration.

1. Apply `db/002_documents.sql` and `db/003_practical.sql` if not already applied.
   These historical migrations precede the new refactor and are for legacy schemas.
2. Apply additive `db/004_mcp.sql`, transactionally with `ON_ERROR_STOP=1`.
   It creates only index identity metadata; documents, vectors, historical tracking
   tables, counters, and functions are preserved. If an administrator creates the
   table, grant the existing runtime role SELECT/INSERT/UPDATE/DELETE on
   `lassy_index_identity`; role/database credentials remain unchanged.
3. Recover/verify the exact model snapshot used to build the existing vectors.
   Set `LOCAL_EMBEDDING_REVISION` to that immutable commit, preserving the old model
   name, dimensions, device-relevant settings, and chunking configuration. The
   shared stack's MPNet baseline is `e8c3b32edf5434bc2275fc9bab85f82640a19130`;
   this baseline alone is not proof that an arbitrary older index used it.
4. Run `lassy-models prepare` under the explicit runtime environment/cache.
5. Explicitly attest the verified identity:

   ```sh
   LASSY_ENV_FILE=/absolute/private/lassy.env uv run --no-sync lassy-models adopt-index
   ```

   If the old configuration **omitted** `LOCAL_EMBEDDING_REVISION`, use
   `adopt-index --legacy-unpinned` after verifying which snapshot it actually used.
   This preserves its original unqualified model fingerprint while loading the
   verified pinned snapshot offline. When the old revision was explicit, omit the
   flag. Adoption inserts identity metadata once and never updates vectors.
6. Deploy the updated HTTP image/start MCP hosts. Verify health and retrieval,
   then resume Lalaland publishing. Historical tracking tables are ignored and
   never automatically deleted; remove them separately only if desired.

Startup refuses unverified populated indexes and mismatched identities. Adoption
refuses to replace an already recorded identity. If the old weights cannot be
established, build a separate empty index with the desired model, republish source
documents, validate retrieval, then switch consumers. Never guess the old identity
or silently change vector spaces. Changing model/revision/dimension, normalization,
token limits, or chunking similarly requires this explicit new-index/reindex flow.

Lalaland's durable outbox still publishes supplied RSS/corpus text and deletions
through HTTP. Use its [backfill guide](../../lalaland/docs/knowledge.md) when
populating a new index. Lassy has no crawling, history-pruning, or evaluation CLI.

## Rollout and rollback

Source changes do not migrate live databases or publish/promote images. Complete
model preparation/adoption before switching the managed API image. Keep the old
image and database backup for rollback. The additive migration and preserved legacy
columns/tables allow the previous service to run again with its original settings;
stop the new writers before rollback. Clients must use the matching tool/response
contract (feedback identifiers and the standalone adapter are retired).
