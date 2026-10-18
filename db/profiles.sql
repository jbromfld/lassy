-- Profiles for RAG MCP server (Copilot pass-through)

ALTER TABLE configuration_profiles
DROP COLUMN IF EXISTS system_config;

-- Default Profile: Copilot handles full RAG generation
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

-- Copilot GPT-4o: Copilot API handles generation directly
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
