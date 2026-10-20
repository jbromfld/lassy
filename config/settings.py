"""Application settings loaded from environment variables."""

from functools import cached_property, lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ChunkingConfig, EmbeddingProviderConfig, RetrievalConfig, VectorStoreConfig

class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ============================================
    # Application
    # ============================================
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    api_port: int = Field(default=8000, alias="API_PORT")
    debug: bool = Field(default=False, alias="DEBUG")

    # ============================================
    # PostgreSQL
    # ============================================
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="rag_service", alias="POSTGRES_DB")
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")

    db_pool_size: int = Field(default=10, ge=1, alias="DB_POOL_SIZE")

    # ============================================
    # pgvector
    # ============================================
    pgvector_dimension: int = Field(default=768, alias="PGVECTOR_DIMENSION")

    # ============================================
    # Local Embeddings (sentence-transformers)
    # ============================================
    local_embedding_model: str = Field(
        default="sentence-transformers/all-mpnet-base-v2",
        alias="LOCAL_EMBEDDING_MODEL",
    )
    local_embedding_revision: str | None = Field(default=None, alias="LOCAL_EMBEDDING_REVISION")
    local_embedding_dimension: int = Field(default=768, alias="LOCAL_EMBEDDING_DIMENSION")
    local_embedding_batch_size: int = Field(default=32, alias="LOCAL_EMBEDDING_BATCH_SIZE")
    local_embedding_device: str = Field(default="cpu", alias="LOCAL_EMBEDDING_DEVICE")

    # ============================================
    # Chunking
    # ============================================
    chunk_strategy: str = Field(default="recursive", alias="CHUNK_STRATEGY")
    chunk_size: int = Field(default=300, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=30, alias="CHUNK_OVERLAP")
    min_chunk_size: int = Field(default=50, alias="MIN_CHUNK_SIZE")
    max_chunk_size: int = Field(default=500, alias="MAX_CHUNK_SIZE")

    # ============================================
    # Retrieval
    # ============================================
    retrieval_top_k: int = Field(default=5, alias="RETRIEVAL_TOP_K")
    hybrid_search_enabled: bool = Field(default=True, alias="HYBRID_SEARCH_ENABLED")
    relevance_threshold: float = Field(default=0.7, alias="RELEVANCE_THRESHOLD")

    enable_reranking: bool = Field(default=False, alias="ENABLE_RERANKING")
    rerank_model: str = Field(default="cross-encoder/ms-marco-MiniLM-L-6-v2", alias="RERANK_MODEL")
    rerank_revision: str | None = Field(default=None, alias="RERANK_REVISION")
    rerank_top_k: int = Field(default=10, alias="RERANK_TOP_K")

    # ============================================
    # Metadata Boosting
    # ============================================
    recency_boost_enabled: bool = Field(default=True, alias="RECENCY_BOOST_ENABLED")

    quality_boost_enabled: bool = Field(default=True, alias="QUALITY_BOOST_ENABLED")
    popularity_boost_enabled: bool = Field(default=True, alias="POPULARITY_BOOST_ENABLED")

    @cached_property
    def vector_store_config(self) -> VectorStoreConfig:
        return VectorStoreConfig(provider="postgresql", dimension=self.pgvector_dimension)

    @cached_property
    def embedding_config(self) -> EmbeddingProviderConfig:
        return EmbeddingProviderConfig(
            provider="local", model=self.local_embedding_model,
            revision=self.local_embedding_revision, dimension=self.local_embedding_dimension, batch_size=self.local_embedding_batch_size,
            device=self.local_embedding_device,
        )

    @cached_property
    def chunking_config(self) -> ChunkingConfig:
        return ChunkingConfig(
            strategy=self.chunk_strategy, chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap, min_chunk_size=self.min_chunk_size,
            max_chunk_size=self.max_chunk_size,
        )

    @cached_property
    def retrieval_config(self) -> RetrievalConfig:
        return RetrievalConfig(
            top_k=self.retrieval_top_k, hybrid_search=self.hybrid_search_enabled,
            relevance_threshold=self.relevance_threshold,
            enable_reranking=self.enable_reranking, rerank_model=self.rerank_model, rerank_revision=self.rerank_revision,
            rerank_top_k=self.rerank_top_k, recency_boost_enabled=self.recency_boost_enabled,
            quality_boost_enabled=self.quality_boost_enabled,
            popularity_boost_enabled=self.popularity_boost_enabled,
        )

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
