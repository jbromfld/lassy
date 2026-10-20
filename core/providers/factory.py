"""Factory for creating provider instances."""

import asyncio
from typing import Optional

from asyncpg import Pool

from config.models import (
    EmbeddingProviderConfig,
    RetrievalConfig,
    VectorStoreConfig,
)

from .base import EmbeddingProvider, VectorStore


class ProviderFactory:
    """Factory for creating provider instances."""

    def __init__(self, db_pool: Optional[Pool] = None):
        """Initialize provider factory.

        Args:
            db_pool: Optional database connection pool for PostgreSQL vector store
        """
        self.db_pool = db_pool
        self._embeddings = {}
        self._rerankers = {}
        self._embedding_lock = asyncio.Lock()

    async def get_embedding_provider(self, config: EmbeddingProviderConfig) -> EmbeddingProvider:
        key = config.model_dump_json()
        async with self._embedding_lock:
            if key not in self._embeddings:
                self._embeddings[key] = await asyncio.to_thread(self.create_embedding_provider, config)
            return self._embeddings[key]

    async def get_reranker(self, config: RetrievalConfig, device: str):
        if not config.enable_reranking:
            return None
        from core.retrieval.reranker import LocalReranker
        key = (config.rerank_model, device, config.rerank_revision)
        async with self._embedding_lock:
            if key not in self._rerankers:
                self._rerankers[key] = await asyncio.to_thread(LocalReranker, *key)
            return self._rerankers[key]

    async def close(self):
        for provider in [*self._embeddings.values(), *self._rerankers.values()]:
            if hasattr(provider, "close"):
                await provider.close()
        self._embeddings.clear()
        self._rerankers.clear()

    def create_embedding_provider(
        self, config: EmbeddingProviderConfig
    ) -> EmbeddingProvider:
        """Create embedding provider instance.

        Args:
            config: Embedding provider configuration

        Returns:
            EmbeddingProvider instance
        """
        if config.provider == "local":
            from .embeddings.local import LocalEmbeddingProvider

            return LocalEmbeddingProvider(config)

        else:
            raise ValueError(f"Unknown embedding provider: {config.provider}")

    def create_vector_store(self, config: VectorStoreConfig) -> VectorStore:
        """Create vector store instance.

        Args:
            config: Vector store configuration

        Returns:
            VectorStore instance
        """
        if config.provider == "postgresql":
            if not self.db_pool:
                raise ValueError("Database pool required for PostgreSQL vector store")

            from .vector_store.pgvector import PgVectorStore

            return PgVectorStore(config, self.db_pool)

        else:
            raise ValueError(f"Unknown vector store provider: {config.provider}")
