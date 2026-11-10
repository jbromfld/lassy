-- Retrieval-only schema. Run once in a fresh database, or after explicit cleanup.
-- PostgreSQL 15+ with the pgvector extension installed on the server.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    content_vector vector(768) NOT NULL,
    content_tsvector tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (content_hash)
);
CREATE INDEX idx_embeddings_vector ON embeddings USING hnsw (content_vector vector_cosine_ops);
CREATE INDEX idx_embeddings_text ON embeddings USING gin (content_tsvector);
CREATE INDEX idx_embeddings_metadata ON embeddings USING gin (metadata);
CREATE INDEX idx_embeddings_source ON embeddings ((metadata->>'source_url'));

-- Additive migration: existing embeddings remain searchable.
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    embedding_revision TEXT,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (collection, document_id)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_document ON embeddings
    ((metadata->>'collection'), (metadata->>'document_id'));

-- Index identity is configuration metadata, not query history.
CREATE TABLE IF NOT EXISTS lassy_index_identity (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    identity JSONB NOT NULL,
    legacy_unpinned BOOLEAN NOT NULL DEFAULT FALSE
);
