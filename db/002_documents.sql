-- Additive migration: existing embeddings remain searchable.
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (collection, document_id)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_document ON embeddings
    ((metadata->>'collection'), (metadata->>'document_id'));
