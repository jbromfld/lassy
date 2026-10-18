-- Cleanup script to reset database
-- Run this if you need to start fresh

-- Drop all tables in order (respecting foreign keys)
DROP TABLE IF EXISTS feedback CASCADE;
DROP TABLE IF EXISTS metrics CASCADE;
DROP TABLE IF EXISTS queries CASCADE;
DROP TABLE IF EXISTS ingestion_jobs CASCADE;
DROP TABLE IF EXISTS embeddings CASCADE;
DROP TABLE IF EXISTS configuration_changes CASCADE;
DROP TABLE IF EXISTS configuration_profiles CASCADE;

-- Drop views
DROP MATERIALIZED VIEW IF EXISTS metrics_by_config CASCADE;

-- Drop functions
DROP FUNCTION IF EXISTS calculate_hybrid_score CASCADE;
DROP FUNCTION IF EXISTS search_embeddings_hybrid CASCADE;
DROP FUNCTION IF EXISTS update_embedding_quality CASCADE;

-- Note: Extensions are kept (uuid-ossp, vector, pg_trgm)


DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE;
DROP FUNCTION IF EXISTS refresh_metrics_by_config() CASCADE;
