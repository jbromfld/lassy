-- ============================================
-- RAG Testing Pipeline - Database Schema
-- PostgreSQL with pgvector extension
-- ============================================

-- psql -U testuser -d rag_service < db/cleanup.sql
-- psql -U testuser -d rag_service < db/init.sql

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================
-- 1. Configuration Management
-- ============================================

-- Configuration Profiles (versioned)
CREATE TABLE configuration_profiles (
    profile_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    profile_name VARCHAR(100) NOT NULL,
    version VARCHAR(20) NOT NULL,
    parent_profile_id UUID REFERENCES configuration_profiles(profile_id),

    -- Complete configuration as JSONB
    provider_config JSONB NOT NULL,
    chunking_config JSONB NOT NULL,
    retrieval_config JSONB NOT NULL,
    generation_config JSONB NOT NULL,

    description TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100),

    UNIQUE(profile_name, version)
);

-- Configuration Changes Log
CREATE TABLE configuration_changes (
    change_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    profile_id UUID REFERENCES configuration_profiles(profile_id),
    parameter_path VARCHAR(200) NOT NULL,
    old_value JSONB,
    new_value JSONB,
    reason TEXT,
    changed_by VARCHAR(100),
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Performance impact (filled in later via analysis)
    performance_impact JSONB
);

-- ============================================
-- 2. Embeddings Storage (pgvector)
-- ============================================

CREATE TABLE embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Content
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,

    -- Vector embedding
    content_vector vector(768) NOT NULL,

    -- Full-text search (auto-generated)
    content_tsvector tsvector GENERATED ALWAYS AS
        (to_tsvector('english', content)) STORED,

    -- Rich metadata (JSONB for flexibility)
    metadata JSONB NOT NULL,

    -- Configuration tracking (nullable for testing without profiles)
    profile_id UUID REFERENCES configuration_profiles(profile_id) ON DELETE SET NULL,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Quality metrics (updated over time)
    avg_feedback_score FLOAT DEFAULT NULL,
    num_feedback INT DEFAULT 0,
    retrieval_count INT DEFAULT 0,
    click_count INT DEFAULT 0,

    -- Deduplication (allow NULL profile_id)
    UNIQUE NULLS NOT DISTINCT (content_hash, profile_id)
);

-- Indexes for embeddings
CREATE INDEX idx_embeddings_vector ON embeddings
    USING hnsw (content_vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_embeddings_tsvector ON embeddings
    USING gin (content_tsvector);

CREATE INDEX idx_embeddings_metadata ON embeddings
    USING gin (metadata);

CREATE INDEX idx_embeddings_profile ON embeddings(profile_id);

CREATE INDEX idx_embeddings_created_at ON embeddings(created_at DESC);

-- Metadata indexes for common filters
CREATE INDEX idx_embeddings_source_url ON embeddings
    ((metadata->>'source_url'));

CREATE INDEX idx_embeddings_source_type ON embeddings
    ((metadata->>'source_type'));

CREATE INDEX idx_embeddings_last_modified ON embeddings
    ((metadata->>'last_modified'));

-- ============================================
-- 3. Queries & Responses
-- ============================================

CREATE TABLE queries (
    query_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_text TEXT NOT NULL,

    -- Configuration used
    profile_id UUID REFERENCES configuration_profiles(profile_id),
    config_snapshot JSONB NOT NULL,

    -- Response
    response JSONB NOT NULL,

    -- Retrieved chunks
    retrieved_chunks UUID[] DEFAULT '{}',

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- User context (optional)
    user_id VARCHAR(100),
    session_id VARCHAR(100)
);

CREATE INDEX idx_queries_profile ON queries(profile_id);
CREATE INDEX idx_queries_created_at ON queries(created_at DESC);
CREATE INDEX idx_queries_user ON queries(user_id);

-- Full-text search on queries
CREATE INDEX idx_queries_text ON queries
    USING gin (to_tsvector('english', query_text));

-- ============================================
-- 4. Metrics Tracking
-- ============================================

CREATE TABLE metrics (
    metric_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_id UUID REFERENCES queries(query_id) NOT NULL,

    -- Latency metrics (milliseconds)
    latency_embedding_ms FLOAT,
    latency_retrieval_ms FLOAT,
    latency_llm_ms FLOAT,
    latency_total_ms FLOAT,

    -- Cost metrics (USD)
    cost_embedding_usd FLOAT DEFAULT 0,
    cost_llm_usd FLOAT DEFAULT 0,
    cost_total_usd FLOAT DEFAULT 0,

    -- Retrieval metrics
    num_chunks_retrieved INT,
    num_chunks_used INT,
    avg_chunk_score FLOAT,

    -- Token usage
    prompt_tokens INT,
    completion_tokens INT,
    total_tokens INT,

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_metrics_query ON metrics(query_id);
CREATE INDEX idx_metrics_created_at ON metrics(created_at DESC);

-- ============================================
-- 5. User Feedback (0-10 Scale)
-- ============================================

CREATE TABLE feedback (
    feedback_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_id UUID REFERENCES queries(query_id) NOT NULL,

    -- Feedback score (0-10)
    score INT NOT NULL CHECK (score BETWEEN 0 AND 10),
    comment TEXT,

    -- User context
    user_id VARCHAR(100),

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_feedback_query ON feedback(query_id);
CREATE INDEX idx_feedback_score ON feedback(score);
CREATE INDEX idx_feedback_created_at ON feedback(created_at DESC);

-- ============================================
-- 6. Ingestion Jobs
-- ============================================

CREATE TABLE ingestion_jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Source information
    source_url TEXT NOT NULL,
    source_type VARCHAR(50) NOT NULL,

    -- Configuration used
    profile_id UUID REFERENCES configuration_profiles(profile_id),

    -- Job status
    status VARCHAR(20) NOT NULL,
    error_message TEXT,

    -- Statistics
    pages_crawled INT DEFAULT 0,
    chunks_created INT DEFAULT 0,
    embeddings_generated INT DEFAULT 0,

    -- Timestamps
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,

    -- Settings
    settings JSONB
);

CREATE INDEX idx_ingestion_jobs_status ON ingestion_jobs(status);
CREATE INDEX idx_ingestion_jobs_started ON ingestion_jobs(started_at DESC);
CREATE INDEX idx_ingestion_jobs_profile ON ingestion_jobs(profile_id);

-- ============================================
-- 7. Materialized Views for Analytics
-- ============================================

-- Metrics by Configuration Profile
CREATE MATERIALIZED VIEW metrics_by_config AS
SELECT
    cp.profile_id,
    cp.profile_name,
    cp.version,

    -- Configuration parameters
    (cp.provider_config->'llm'->>'model') as llm_model,
    (cp.provider_config->'llm'->>'temperature')::float as llm_temperature,
    (cp.chunking_config->>'chunk_size')::int as chunk_size,
    (cp.retrieval_config->>'top_k')::int as top_k,

    -- Aggregated metrics
    COUNT(DISTINCT q.query_id) as total_queries,
    AVG(m.latency_total_ms) as avg_latency_ms,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m.latency_total_ms) as median_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY m.latency_total_ms) as p95_latency_ms,

    AVG(m.cost_total_usd) as avg_cost_usd,
    SUM(m.cost_total_usd) as total_cost_usd,

    AVG(f.score) as avg_feedback_score,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.score) as median_feedback_score,
    COUNT(f.feedback_id) FILTER (WHERE f.score >= 7) as satisfied_count,
    COUNT(f.feedback_id) as total_feedback,

    -- Satisfaction rate
    CASE
        WHEN COUNT(f.feedback_id) > 0
        THEN ROUND(
            COUNT(f.feedback_id) FILTER (WHERE f.score >= 7)::numeric /
            COUNT(f.feedback_id)::numeric,
            3
        )
        ELSE NULL
    END as satisfaction_rate,

    MAX(q.created_at) as last_used_at
FROM configuration_profiles cp
LEFT JOIN queries q ON cp.profile_id = q.profile_id
LEFT JOIN metrics m ON q.query_id = m.query_id
LEFT JOIN feedback f ON q.query_id = f.query_id
WHERE cp.is_active = true
GROUP BY
    cp.profile_id,
    cp.profile_name,
    cp.version,
    llm_model,
    llm_temperature,
    chunk_size,
    top_k;

-- Refresh function for materialized view
CREATE OR REPLACE FUNCTION refresh_metrics_by_config()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW metrics_by_config;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 8. Functions & Triggers
-- ============================================

-- Update timestamp trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_embeddings_updated_at
    BEFORE UPDATE ON embeddings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Update embedding quality metrics from feedback
CREATE OR REPLACE FUNCTION update_embedding_quality()
RETURNS TRIGGER AS $$
BEGIN
    -- Update avg_feedback_score and num_feedback for chunks used in this query
    UPDATE embeddings
    SET
        avg_feedback_score = (
            SELECT AVG(f.score)
            FROM feedback f
            JOIN queries q ON f.query_id = q.query_id
            WHERE embeddings.id = ANY(q.retrieved_chunks)
        ),
        num_feedback = (
            SELECT COUNT(*)
            FROM feedback f
            JOIN queries q ON f.query_id = q.query_id
            WHERE embeddings.id = ANY(q.retrieved_chunks)
        )
    WHERE id = ANY(
        SELECT unnest(retrieved_chunks)
        FROM queries
        WHERE query_id = NEW.query_id
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_embedding_quality
    AFTER INSERT ON feedback
    FOR EACH ROW
    EXECUTE FUNCTION update_embedding_quality();

-- ============================================
-- 9. Initial Configuration Profiles
-- ============================================

-- Default Profile: Copilot auto model selection
INSERT INTO configuration_profiles (profile_name, version, provider_config, chunking_config, retrieval_config, generation_config, description)
VALUES (
    'default',
    '1.0.0',
    '{
        "vector_store": {"provider": "postgresql", "dimension": 768},
        "embedding": {"provider": "local", "model": "sentence-transformers/all-mpnet-base-v2", "dimension": 768},
        "llm": {"provider": "copilot", "model": "auto", "temperature": 0.7, "max_tokens": 2000}
    }'::jsonb,
    '{"strategy": "recursive", "chunk_size": 300, "chunk_overlap": 30, "min_chunk_size": 50, "max_chunk_size": 500}'::jsonb,
    '{"top_k": 5, "hybrid_search": true, "vector_weight": 0.7, "bm25_weight": 0.3, "relevance_threshold": 0.0}'::jsonb,
    '{"temperature": 0.7, "max_tokens": 2000, "include_sources": true, "citation_format": "inline"}'::jsonb,
    'Default Copilot profile - RAG retrieval with Copilot auto model selection'
) ON CONFLICT (profile_name, version) DO UPDATE SET
    provider_config = EXCLUDED.provider_config,
    chunking_config = EXCLUDED.chunking_config,
    retrieval_config = EXCLUDED.retrieval_config,
    generation_config = EXCLUDED.generation_config,
    description = EXCLUDED.description,
    is_active = TRUE;

-- Copilot GPT-4o: explicit model pinning
INSERT INTO configuration_profiles (profile_name, version, provider_config, chunking_config, retrieval_config, generation_config, description)
VALUES (
    'copilot-gpt4o',
    '1.0.0',
    '{
        "vector_store": {"provider": "postgresql", "dimension": 768},
        "embedding": {"provider": "local", "model": "sentence-transformers/all-mpnet-base-v2", "dimension": 768},
        "llm": {"provider": "copilot", "model": "gpt-4o", "temperature": 0.7, "max_tokens": 2000}
    }'::jsonb,
    '{"strategy": "recursive", "chunk_size": 300, "chunk_overlap": 30, "min_chunk_size": 50, "max_chunk_size": 500}'::jsonb,
    '{"top_k": 5, "hybrid_search": true, "vector_weight": 0.7, "bm25_weight": 0.3, "relevance_threshold": 0.0}'::jsonb,
    '{"temperature": 0.7, "max_tokens": 2000, "include_sources": true, "citation_format": "inline"}'::jsonb,
    'GitHub Copilot GPT-4o - RAG retrieval with Copilot generation'
) ON CONFLICT (profile_name, version) DO UPDATE SET
    provider_config = EXCLUDED.provider_config,
    chunking_config = EXCLUDED.chunking_config,
    retrieval_config = EXCLUDED.retrieval_config,
    generation_config = EXCLUDED.generation_config,
    description = EXCLUDED.description,
    is_active = TRUE;

-- ============================================
-- Grant Permissions
-- ============================================

-- Grant usage on schema
GRANT USAGE ON SCHEMA public TO testuser;

-- Grant all privileges on all tables
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO testuser;

-- Grant all privileges on all sequences
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO testuser;

-- Grant execute on all functions
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO testuser;

-- ============================================
-- Database Ready
-- ============================================

-- Set default configuration
COMMENT ON DATABASE rag_service IS 'RAG Testing Pipeline - Vector search with metadata tracking and comprehensive metrics';
