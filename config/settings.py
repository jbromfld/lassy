"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    app_name: str = Field(default="rag-mcp-server", alias="APP_NAME")
    environment: str = Field(default="local", alias="ENVIRONMENT")
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

    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")

    @property
    def database_url(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def async_database_url(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    # ============================================
    # pgvector
    # ============================================
    pgvector_dimension: int = Field(default=768, alias="PGVECTOR_DIMENSION")
    pgvector_index_type: str = Field(default="hnsw", alias="PGVECTOR_INDEX_TYPE")
    pgvector_hnsw_m: int = Field(default=16, alias="PGVECTOR_HNSW_M")
    pgvector_hnsw_ef_construction: int = Field(default=64, alias="PGVECTOR_HNSW_EF_CONSTRUCTION")

    # ============================================
    # Local Embeddings (sentence-transformers)
    # ============================================
    local_embedding_model: str = Field(
        default="sentence-transformers/all-mpnet-base-v2",
        alias="LOCAL_EMBEDDING_MODEL",
    )
    local_embedding_dimension: int = Field(default=768, alias="LOCAL_EMBEDDING_DIMENSION")
    local_embedding_batch_size: int = Field(default=32, alias="LOCAL_EMBEDDING_BATCH_SIZE")
    local_embedding_device: str = Field(default="cpu", alias="LOCAL_EMBEDDING_DEVICE")

    # ============================================
    # GitHub Copilot
    # ============================================
    copilot_api_key: Optional[str] = Field(default=None, alias="COPILOT_API_KEY")
    copilot_temperature: float = Field(default=0.7, alias="COPILOT_TEMPERATURE")
    copilot_max_tokens: int = Field(default=2000, alias="COPILOT_MAX_TOKENS")

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
    vector_weight: float = Field(default=0.7, alias="VECTOR_WEIGHT")
    bm25_weight: float = Field(default=0.3, alias="BM25_WEIGHT")
    relevance_threshold: float = Field(default=0.7, alias="RELEVANCE_THRESHOLD")

    enable_reranking: bool = Field(default=False, alias="ENABLE_RERANKING")
    rerank_model: Optional[str] = Field(default=None, alias="RERANK_MODEL")
    rerank_top_k: int = Field(default=10, alias="RERANK_TOP_K")

    enable_query_expansion: bool = Field(default=False, alias="ENABLE_QUERY_EXPANSION")
    expansion_method: Optional[str] = Field(default=None, alias="EXPANSION_METHOD")
    max_expansions: int = Field(default=3, alias="MAX_EXPANSIONS")

    # ============================================
    # Metadata Boosting
    # ============================================
    recency_boost_enabled: bool = Field(default=True, alias="RECENCY_BOOST_ENABLED")
    recency_very_recent_days: int = Field(default=30, alias="RECENCY_VERY_RECENT_DAYS")
    recency_recent_days: int = Field(default=90, alias="RECENCY_RECENT_DAYS")
    recency_moderate_days: int = Field(default=180, alias="RECENCY_MODERATE_DAYS")
    recency_decay_rate: float = Field(default=730.0, alias="RECENCY_DECAY_RATE")

    quality_boost_enabled: bool = Field(default=True, alias="QUALITY_BOOST_ENABLED")
    quality_min_feedback: int = Field(default=3, alias="QUALITY_MIN_FEEDBACK")
    popularity_boost_enabled: bool = Field(default=True, alias="POPULARITY_BOOST_ENABLED")

    # ============================================
    # Generation
    # ============================================
    prompt_template: str = Field(default="default", alias="PROMPT_TEMPLATE")
    prompt_version: str = Field(default="1.0", alias="PROMPT_VERSION")
    include_sources: bool = Field(default=True, alias="INCLUDE_SOURCES")
    max_context_tokens: int = Field(default=8000, alias="MAX_CONTEXT_TOKENS")
    response_format: str = Field(default="markdown", alias="RESPONSE_FORMAT")
    include_citations: bool = Field(default=True, alias="INCLUDE_CITATIONS")
    citation_format: str = Field(default="inline", alias="CITATION_FORMAT")

    content_filter_enabled: bool = Field(default=True, alias="CONTENT_FILTER_ENABLED")
    pii_detection_enabled: bool = Field(default=False, alias="PII_DETECTION_ENABLED")
    toxic_filter_enabled: bool = Field(default=False, alias="TOXIC_FILTER_ENABLED")

    # ============================================
    # Ingestion / Scraping
    # ============================================
    scrape_max_depth: int = Field(default=3, alias="SCRAPE_MAX_DEPTH")
    scrape_max_pages: int = Field(default=100, alias="SCRAPE_MAX_PAGES")
    scrape_timeout_seconds: int = Field(default=10, alias="SCRAPE_TIMEOUT_SECONDS")
    scrape_user_agent: str = Field(default="RAG-MCP-Bot/1.0", alias="SCRAPE_USER_AGENT")

    playwright_headless: bool = Field(default=True, alias="PLAYWRIGHT_HEADLESS")
    playwright_timeout: int = Field(default=30000, alias="PLAYWRIGHT_TIMEOUT")

    # ============================================
    # Monitoring
    # ============================================
    log_queries: bool = Field(default=True, alias="LOG_QUERIES")
    log_responses: bool = Field(default=True, alias="LOG_RESPONSES")
    log_format: str = Field(default="json", alias="LOG_FORMAT")

    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    metrics_export_interval_seconds: int = Field(default=60, alias="METRICS_EXPORT_INTERVAL_SECONDS")


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
