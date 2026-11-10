"""Configuration models using Pydantic."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# ============================================
# Enumerations
# ============================================


class VectorStoreProvider(str, Enum):
    POSTGRESQL = "postgresql"


class EmbeddingProvider(str, Enum):
    LOCAL = "local"


class ChunkStrategy(str, Enum):
    FIXED = "fixed"
    RECURSIVE = "recursive"
    SEMANTIC = "semantic"


class FusionMethod(str, Enum):
    RRF = "rrf"
    WEIGHTED = "weighted"
    CASCADE = "cascade"


# ============================================
# Provider Configurations
# ============================================


class VectorStoreConfig(BaseModel):
    """Vector store configuration (pgvector)."""

    provider: VectorStoreProvider
    url: Optional[str] = None
    index_name: str = "knowledge_base"
    dimension: int = 768

    hnsw_m: int = Field(default=16, description="HNSW graph connections")
    hnsw_ef_construction: int = Field(default=64, description="HNSW construction parameter")

    class Config:
        use_enum_values = True


class EmbeddingProviderConfig(BaseModel):
    """Embedding provider configuration (local sentence-transformers)."""

    provider: EmbeddingProvider
    model: str
    revision: Optional[str] = None
    dimension: int = Field(default=768, ge=1)
    batch_size: int = Field(default=32, ge=1, le=256)
    queue_timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_pending: int = Field(default=16, ge=1, le=256)
    normalize: bool = True
    device: str = "cpu"  # cpu or cuda

    class Config:
        use_enum_values = True


# ============================================
# Processing Configurations
# ============================================


class ChunkingConfig(BaseModel):
    """Document chunking configuration."""

    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    chunk_size: int = Field(default=300, ge=50, le=1000, description="Words per chunk")
    chunk_overlap: int = Field(default=30, ge=0, le=200, description="Overlap in words")
    min_chunk_size: int = Field(default=50, ge=10, le=500)
    max_chunk_size: int = Field(default=500, ge=100, le=2000)

    separators: List[str] = Field(default=["\n\n", "\n", ". ", " "])
    keep_separator: bool = True
    semantic_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    preserve_metadata: bool = True

    @model_validator(mode='after')
    def validate_sizes(self):
        if not self.min_chunk_size <= self.chunk_size <= self.max_chunk_size:
            raise ValueError('Require min_chunk_size <= chunk_size <= max_chunk_size')
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('chunk_overlap must be smaller than chunk_size')
        return self

    class Config:
        use_enum_values = True


class RetrievalConfig(BaseModel):
    """Retrieval configuration."""

    top_k: int = Field(default=5, ge=1, le=100)
    hybrid_search: bool = True
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    bm25_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    fusion_method: FusionMethod = FusionMethod.RRF

    relevance_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)

    date_filter: Optional[Dict[str, Any]] = None
    source_filter: Optional[List[str]] = None

    enable_reranking: bool = False
    rerank_revision: Optional[str] = None
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_top_k: int = Field(default=10, ge=1, le=100)
    queue_timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_pending: int = Field(default=16, ge=1, le=256)

    enable_query_expansion: bool = False
    expansion_method: Optional[str] = None
    max_expansions: int = Field(default=3, ge=1, le=10)

    recency_boost_enabled: bool = True
    quality_boost_enabled: bool = True
    popularity_boost_enabled: bool = True

    class Config:
        use_enum_values = True
