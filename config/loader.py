"""Configuration profile loader."""

import json
from functools import lru_cache
from typing import Optional
from uuid import UUID

import asyncpg
from asyncpg import Connection, Pool

from .models import (
    ChunkingConfig,
    ConfigurationProfile,
    EmbeddingProviderConfig,
    GenerationConfig,
    LLMProviderConfig,
    ProviderConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from .settings import Settings, get_settings


class ConfigLoader:
    """Load and manage configuration profiles."""

    def __init__(self, settings: Settings, db_pool: Optional[Pool] = None):
        """Initialize config loader.

        Args:
            settings: Application settings
            db_pool: Optional database connection pool
        """
        self.settings = settings
        self.db_pool = db_pool

    async def get_profile_by_name(
        self, profile_name: str, version: Optional[str] = None
    ) -> ConfigurationProfile:
        """Load configuration profile by name.

        Args:
            profile_name: Name of the profile
            version: Optional version (defaults to latest)

        Returns:
            ConfigurationProfile instance

        Raises:
            ValueError: If profile not found
        """
        if not self.db_pool:
            raise RuntimeError("Database pool not initialized")

        async with self.db_pool.acquire() as conn:
            if version:
                query = """
                    SELECT * FROM configuration_profiles
                    WHERE profile_name = $1 AND version = $2
                """
                row = await conn.fetchrow(query, profile_name, version)
            else:
                # Get latest version
                query = """
                    SELECT * FROM configuration_profiles
                    WHERE profile_name = $1
                    ORDER BY created_at DESC
                    LIMIT 1
                """
                row = await conn.fetchrow(query, profile_name)

            if not row:
                raise ValueError(
                    f"Profile '{profile_name}'"
                    + (f" version '{version}'" if version else "")
                    + " not found"
                )

            return self._row_to_profile(row)

    async def get_profile_by_id(self, profile_id: UUID) -> ConfigurationProfile:
        """Load configuration profile by ID.

        Args:
            profile_id: UUID of the profile

        Returns:
            ConfigurationProfile instance

        Raises:
            ValueError: If profile not found
        """
        if not self.db_pool:
            raise RuntimeError("Database pool not initialized")

        async with self.db_pool.acquire() as conn:
            query = "SELECT * FROM configuration_profiles WHERE profile_id = $1"
            row = await conn.fetchrow(query, profile_id)

            if not row:
                raise ValueError(f"Profile with ID '{profile_id}' not found")

            return self._row_to_profile(row)

    async def save_profile(self, profile: ConfigurationProfile) -> None:
        """Save configuration profile to database.

        Args:
            profile: Configuration profile to save
        """
        if not self.db_pool:
            raise RuntimeError("Database pool not initialized")

        async with self.db_pool.acquire() as conn:
            query = """
                INSERT INTO configuration_profiles (
                    profile_id, profile_name, version, parent_profile_id,
                    provider_config, chunking_config, retrieval_config,
                    generation_config, description, is_active,
                    created_at, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                ON CONFLICT (profile_name, version) DO UPDATE SET
                    provider_config = EXCLUDED.provider_config,
                    chunking_config = EXCLUDED.chunking_config,
                    retrieval_config = EXCLUDED.retrieval_config,
                    generation_config = EXCLUDED.generation_config,
                    description = EXCLUDED.description,
                    is_active = EXCLUDED.is_active
            """

            await conn.execute(
                query,
                profile.profile_id,
                profile.profile_name,
                profile.version,
                profile.parent_profile_id,
                json.dumps(profile.provider_config.model_dump(mode="json")),
                json.dumps(profile.chunking_config.model_dump(mode="json")),
                json.dumps(profile.retrieval_config.model_dump(mode="json")),
                json.dumps(profile.generation_config.model_dump(mode="json")),
                profile.description,
                profile.is_active,
                profile.created_at,
                profile.created_by,
            )

    def create_default_profile(self) -> ConfigurationProfile:
        """Create default configuration profile from environment settings.

        Returns:
            ConfigurationProfile with default settings
        """
        s = self.settings

        # Vector store config (pgvector only)
        vector_store = VectorStoreConfig(
            provider="postgresql",
            url=s.database_url,
            dimension=s.pgvector_dimension,
            hnsw_m=s.pgvector_hnsw_m,
            hnsw_ef_construction=s.pgvector_hnsw_ef_construction,
        )

        # Embedding config (local sentence-transformers only)
        embedding = EmbeddingProviderConfig(
            provider="local",
            model=s.local_embedding_model,
            dimension=s.local_embedding_dimension,
            batch_size=s.local_embedding_batch_size,
            device=s.local_embedding_device,
        )

        # LLM config
        llm = LLMProviderConfig(
            provider="copilot",
            model="auto",
            temperature=s.copilot_temperature,
            max_tokens=s.copilot_max_tokens,
            api_key=s.copilot_api_key,
        )

        # Provider config
        provider_config = ProviderConfig(
            vector_store=vector_store,
            embedding=embedding,
            llm=llm,
        )

        # Chunking config
        chunking_config = ChunkingConfig(
            strategy=s.chunk_strategy,
            chunk_size=s.chunk_size,
            chunk_overlap=s.chunk_overlap,
            min_chunk_size=s.min_chunk_size,
            max_chunk_size=s.max_chunk_size,
        )

        # Retrieval config
        retrieval_config = RetrievalConfig(
            top_k=s.retrieval_top_k,
            hybrid_search=s.hybrid_search_enabled,
            vector_weight=s.vector_weight,
            bm25_weight=s.bm25_weight,
            relevance_threshold=s.relevance_threshold,
            enable_reranking=s.enable_reranking,
            rerank_model=s.rerank_model,
            rerank_top_k=s.rerank_top_k,
            enable_query_expansion=s.enable_query_expansion,
            expansion_method=s.expansion_method,
            max_expansions=s.max_expansions,
            recency_boost_enabled=s.recency_boost_enabled,
            quality_boost_enabled=s.quality_boost_enabled,
            popularity_boost_enabled=s.popularity_boost_enabled,
        )

        # Generation config
        generation_config = GenerationConfig(
            prompt_template=s.prompt_template,
            prompt_version=s.prompt_version,
            include_sources=s.include_sources,
            max_context_tokens=s.max_context_tokens,
            response_format=s.response_format,
            include_citations=s.include_citations,
            citation_format=s.citation_format,
            content_filter=s.content_filter_enabled,
            pii_detection=s.pii_detection_enabled,
            toxic_filter=s.toxic_filter_enabled,
        )

        # Create profile
        return ConfigurationProfile(
            profile_name="default",
            version="1.0.0",
            provider_config=provider_config,
            chunking_config=chunking_config,
            retrieval_config=retrieval_config,
            generation_config=generation_config,
            description="Default configuration from environment variables",
            created_by="system",
        )

    def _row_to_profile(self, row) -> ConfigurationProfile:
        """Convert database row to ConfigurationProfile.

        Args:
            row: Database row

        Returns:
            ConfigurationProfile instance
        """
        # Parse JSON columns (asyncpg returns them as dicts already, but handle both cases)
        provider_config_data = row["provider_config"] if isinstance(row["provider_config"], dict) else json.loads(row["provider_config"])
        chunking_config_data = row["chunking_config"] if isinstance(row["chunking_config"], dict) else json.loads(row["chunking_config"])
        retrieval_config_data = row["retrieval_config"] if isinstance(row["retrieval_config"], dict) else json.loads(row["retrieval_config"])
        generation_config_data = row["generation_config"] if isinstance(row["generation_config"], dict) else json.loads(row["generation_config"])
        return ConfigurationProfile(
            profile_id=row["profile_id"],
            profile_name=row["profile_name"],
            version=row["version"],
            parent_profile_id=row["parent_profile_id"],
            provider_config=ProviderConfig(**provider_config_data),
            chunking_config=ChunkingConfig(**chunking_config_data),
            retrieval_config=RetrievalConfig(**retrieval_config_data),
            generation_config=GenerationConfig(**generation_config_data),
            description=row["description"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            created_by=row["created_by"],
        )


# Singleton instance (set by application on startup)
_config_loader: Optional[ConfigLoader] = None


def set_config_loader(loader: ConfigLoader) -> None:
    """Set global config loader instance."""
    global _config_loader
    _config_loader = loader


def get_config_loader() -> ConfigLoader:
    """Get global config loader instance.

    Returns:
        ConfigLoader instance

    Raises:
        RuntimeError: If loader not initialized
    """
    if _config_loader is None:
        # Fallback: create with settings but no DB pool
        return ConfigLoader(get_settings())
    return _config_loader
