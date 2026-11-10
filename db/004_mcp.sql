-- Additive only: preserve all existing vectors, documents, and historical tables.
-- Index identity is configuration metadata, not query history.
CREATE TABLE IF NOT EXISTS lassy_index_identity (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    identity JSONB NOT NULL,
    legacy_unpinned BOOLEAN NOT NULL DEFAULT FALSE
);
