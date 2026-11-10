"""Explicit environment configuration shared by MCP, HTTP, and model preparation."""

import os
from enum import Enum
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MPNET_REVISION = "e8c3b32edf5434bc2275fc9bab85f82640a19130"
RERANK_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"


class ChunkStrategy(str, Enum):
    FIXED = "fixed"
    RECURSIVE = "recursive"
    SEMANTIC = "semantic"  # Existing alias for recursive splitting.


class ChunkingConfig(BaseModel):
    # Preserve the original serialized identity, avoiding gratuitous re-embedding.
    model_config = ConfigDict(use_enum_values=True)
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    chunk_size: int = Field(default=300, ge=50, le=1000)
    chunk_overlap: int = Field(default=30, ge=0, le=200)
    min_chunk_size: int = Field(default=50, ge=10, le=500)
    max_chunk_size: int = Field(default=500, ge=100, le=2000)
    separators: list[str] = ["\n\n", "\n", ". ", " "]
    keep_separator: bool = True
    semantic_threshold: float = 0.7
    preserve_metadata: bool = True

    @model_validator(mode="after")
    def validate_sizes(self):
        if not self.min_chunk_size <= self.chunk_size <= self.max_chunk_size:
            raise ValueError("Require min_chunk_size <= chunk_size <= max_chunk_size")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class EmbeddingConfig(BaseModel):
    model: str = "sentence-transformers/all-mpnet-base-v2"
    revision: str = Field(default=MPNET_REVISION, pattern=r"^[0-9a-f]{40}$")
    dimension: int = Field(default=768, ge=1, le=2000)
    batch_size: int = Field(default=32, ge=1, le=256)
    normalize: bool = True
    device: str = "cpu"
    queue_timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_pending: int = Field(default=16, ge=1, le=256)


class RetrievalConfig(BaseModel):
    top_k: int = Field(default=5, ge=1, le=100)
    hybrid_search: bool = True
    relevance_threshold: float = Field(default=0.7, ge=0, le=1)
    recency_boost_enabled: bool = True
    enable_reranking: bool = False
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_revision: str = Field(default=RERANK_REVISION, pattern=r"^[0-9a-f]{40}$")
    rerank_top_k: int = Field(default=10, ge=1, le=100)
    queue_timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_pending: int = Field(default=16, ge=1, le=256)


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")
    hf_home: str | None = None
    local_embedding_model: str = "sentence-transformers/all-mpnet-base-v2"
    local_embedding_revision: str = Field(
        default=MPNET_REVISION, pattern=r"^[0-9a-f]{40}$"
    )
    local_embedding_dimension: int = Field(default=768, ge=1, le=2000)
    local_embedding_batch_size: int = Field(default=32, ge=1, le=256)
    local_embedding_device: str = "cpu"
    inference_queue_timeout_seconds: float = Field(default=30, gt=0, le=300)
    inference_max_pending: int = Field(default=16, ge=1, le=256)
    enable_reranking: bool = False
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_revision: str = Field(default=RERANK_REVISION, pattern=r"^[0-9a-f]{40}$")
    rerank_top_k: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def explicit_custom_revisions(self):
        if (
            self.local_embedding_model != "sentence-transformers/all-mpnet-base-v2"
            and self.local_embedding_revision == MPNET_REVISION
        ):
            raise ValueError("Set LOCAL_EMBEDDING_REVISION for a custom model")
        if (
            self.rerank_model != "cross-encoder/ms-marco-MiniLM-L-6-v2"
            and self.rerank_revision == RERANK_REVISION
        ):
            raise ValueError("Set RERANK_REVISION for a custom model")
        return self

    @cached_property
    def embedding_config(self):
        return EmbeddingConfig(
            model=self.local_embedding_model,
            revision=self.local_embedding_revision,
            dimension=self.local_embedding_dimension,
            batch_size=self.local_embedding_batch_size,
            device=self.local_embedding_device,
            queue_timeout_seconds=self.inference_queue_timeout_seconds,
            max_pending=self.inference_max_pending,
        )

    @cached_property
    def retrieval_config(self):
        return RetrievalConfig(
            **{
                k: getattr(self, k)
                for k in RetrievalConfig.model_fields
                if hasattr(self, k)
            },
            queue_timeout_seconds=self.inference_queue_timeout_seconds,
            max_pending=self.inference_max_pending,
        )


class Settings(ModelSettings):
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "rag_service"
    postgres_user: str
    postgres_password: SecretStr
    db_pool_size: int = Field(default=10, ge=1, le=100)
    pgvector_dimension: int = Field(default=768, ge=1, le=2000)
    document_lock_timeout_seconds: float = Field(default=30, gt=0, le=300)
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_host: str = "127.0.0.1"
    chunk_strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    chunk_size: int = 300
    chunk_overlap: int = 30
    min_chunk_size: int = 50
    max_chunk_size: int = 500
    retrieval_top_k: int = Field(default=5, ge=1, le=100)
    hybrid_search_enabled: bool = True
    relevance_threshold: float = Field(default=0.7, ge=0, le=1)
    recency_boost_enabled: bool = True

    @model_validator(mode="after")
    def processing(self):
        self.chunking_config
        self.embedding_config
        self.retrieval_config
        if self.pgvector_dimension != self.local_embedding_dimension:
            raise ValueError("Embedding and vector-store dimensions must match")
        return self

    @cached_property
    def chunking_config(self):
        return ChunkingConfig(
            strategy=self.chunk_strategy,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            min_chunk_size=self.min_chunk_size,
            max_chunk_size=self.max_chunk_size,
        )

    @cached_property
    def retrieval_config(self):
        return RetrievalConfig(
            top_k=self.retrieval_top_k,
            hybrid_search=self.hybrid_search_enabled,
            relevance_threshold=self.relevance_threshold,
            recency_boost_enabled=self.recency_boost_enabled,
            enable_reranking=self.enable_reranking,
            rerank_model=self.rerank_model,
            rerank_revision=self.rerank_revision,
            rerank_top_k=self.rerank_top_k,
            queue_timeout_seconds=self.inference_queue_timeout_seconds,
            max_pending=self.inference_max_pending,
        )


def load_settings(model_only=False):
    env_file = os.environ.get("LASSY_ENV_FILE")
    if env_file:
        env_file = Path(env_file).expanduser().resolve(strict=True)
    settings = (ModelSettings if model_only else Settings)(_env_file=env_file)
    if settings.hf_home:
        cache = Path(settings.hf_home).expanduser()
        if not cache.is_absolute() and env_file:
            cache = env_file.parent / cache
        os.environ["HF_HOME"] = str(cache.resolve())
    return settings
