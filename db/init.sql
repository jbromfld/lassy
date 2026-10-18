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
    avg_feedback_score FLOAT,
    num_feedback INT NOT NULL DEFAULT 0,
    retrieval_count INT NOT NULL DEFAULT 0,
    UNIQUE (content_hash)
);
CREATE INDEX idx_embeddings_vector ON embeddings USING hnsw (content_vector vector_cosine_ops);
CREATE INDEX idx_embeddings_text ON embeddings USING gin (content_tsvector);
CREATE INDEX idx_embeddings_metadata ON embeddings USING gin (metadata);
CREATE INDEX idx_embeddings_source ON embeddings ((metadata->>'source_url'));

CREATE TABLE queries (
    query_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    response JSONB NOT NULL,
    retrieved_chunks UUID[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id UUID UNIQUE NOT NULL REFERENCES queries(query_id),
    latency_retrieval_ms FLOAT NOT NULL,
    latency_rerank_ms FLOAT NOT NULL DEFAULT 0,
    latency_total_ms FLOAT NOT NULL,
    num_chunks_retrieved INT NOT NULL,
    avg_chunk_score FLOAT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE feedback (
    feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id UUID NOT NULL REFERENCES queries(query_id),
    score INT NOT NULL CHECK (score BETWEEN 0 AND 10),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_feedback_query ON feedback (query_id);

CREATE OR REPLACE FUNCTION update_embedding_quality() RETURNS TRIGGER AS $$
BEGIN
    UPDATE embeddings e SET
        avg_feedback_score = (SELECT AVG(f.score) FROM feedback f JOIN queries q ON f.query_id = q.query_id WHERE e.id = ANY(q.retrieved_chunks)),
        num_feedback = (SELECT COUNT(*) FROM feedback f JOIN queries q ON f.query_id = q.query_id WHERE e.id = ANY(q.retrieved_chunks))
    WHERE e.id = ANY((SELECT retrieved_chunks FROM queries WHERE query_id = NEW.query_id)::uuid[]);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trigger_update_embedding_quality AFTER INSERT ON feedback
    FOR EACH ROW EXECUTE FUNCTION update_embedding_quality();
