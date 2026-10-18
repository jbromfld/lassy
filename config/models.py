"""Configuration models using Pydantic."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


# ============================================
# Enumerations
# ============================================


class VectorStoreProvider(str, Enum):
    POSTGRESQL = "postgresql"


class EmbeddingProvider(str, Enum):
    LOCAL = "local"


class LLMProvider(str, Enum):
    COPILOT = "copilot"


class ChunkStrategy(str, Enum):
    FIXED = "fixed"
    RECURSIVE = "recursive"
    SEMANTIC = "semantic"


class FusionMethod(str, Enum):
    RRF = "rrf"
    WEIGHTED = "weighted"
    CASCADE = "cascade"


class ResponseFormat(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"


class CitationFormat(str, Enum):
    INLINE = "inline"
    FOOTNOTE = "footnote"
    LIST = "list"


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
    dimension: int = 768
    batch_size: int = 32
    normalize: bool = True
    device: str = "cpu"  # cpu or cuda

    class Config:
        use_enum_values = True


class LLMProviderConfig(BaseModel):
    """LLM provider configuration."""

    provider: LLMProvider
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1, le=32000)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)

    # Copilot API key (optional — falls back to COPILOT_API_KEY env var)
    api_key: Optional[str] = None

    class Config:
        use_enum_values = True


class ProviderConfig(BaseModel):
    """Combined provider configuration."""

    vector_store: VectorStoreConfig
    embedding: EmbeddingProviderConfig
    llm: LLMProviderConfig


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

    @field_validator("chunk_overlap")
    @classmethod
    def validate_overlap(cls, v: int, info) -> int:
        return v

    class Config:
        use_enum_values = True


class RetrievalConfig(BaseModel):
    """Retrieval configuration."""

    top_k: int = Field(default=5, ge=1, le=50)
    hybrid_search: bool = True
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    bm25_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    fusion_method: FusionMethod = FusionMethod.RRF

    relevance_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)

    date_filter: Optional[Dict[str, Any]] = None
    source_filter: Optional[List[str]] = None

    enable_reranking: bool = False
    rerank_model: Optional[str] = None
    rerank_top_k: int = Field(default=10, ge=1, le=100)

    enable_query_expansion: bool = False
    expansion_method: Optional[str] = None
    max_expansions: int = Field(default=3, ge=1, le=10)

    recency_boost_enabled: bool = True
    quality_boost_enabled: bool = True
    popularity_boost_enabled: bool = True

    @field_validator("bm25_weight")
    @classmethod
    def validate_weights(cls, v: float, info) -> float:
        return v

    class Config:
        use_enum_values = True


class GenerationConfig(BaseModel):
    """LLM generation configuration."""

    prompt_template: str = "default"
    prompt_version: str = "1.0"

    include_sources: bool = True
    max_context_tokens: int = Field(default=8000, ge=1000, le=32000)

    response_format: ResponseFormat = ResponseFormat.MARKDOWN
    include_citations: bool = True
    citation_format: CitationFormat = CitationFormat.INLINE

    content_filter: bool = True
    pii_detection: bool = False
    toxic_filter: bool = False

    class Config:
        use_enum_values = True


class ConfigurationProfile(BaseModel):
    """Complete configuration profile."""

    profile_id: UUID = Field(default_factory=uuid4)
    profile_name: str = Field(..., min_length=1, max_length=100)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")

    parent_profile_id: Optional[UUID] = None

    provider_config: ProviderConfig
    chunking_config: ChunkingConfig
    retrieval_config: RetrievalConfig
    generation_config: GenerationConfig

    description: Optional[str] = None
    is_active: bool = True

    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "profile_name": "default",
                "version": "1.0.0",
                "provider_config": {
                    "vector_store": {"provider": "postgresql", "dimension": 768},
                    "embedding": {
                        "provider": "local",
                        "model": "sentence-transformers/all-mpnet-base-v2",
                        "dimension": 768,
                    },
                    "llm": {"provider": "copilot", "model": "auto"},
                },
                "chunking_config": {"strategy": "recursive", "chunk_size": 300, "chunk_overlap": 30},
                "retrieval_config": {"top_k": 5, "hybrid_search": True, "vector_weight": 0.7},
                "generation_config": {"prompt_template": "default", "max_context_tokens": 8000},
            }
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "profile_id": str(self.profile_id),
            "profile_name": self.profile_name,
            "version": self.version,
            "parent_profile_id": str(self.parent_profile_id) if self.parent_profile_id else None,
            "provider_config": self.provider_config.model_dump(mode="json"),
            "chunking_config": self.chunking_config.model_dump(mode="json"),
            "retrieval_config": self.retrieval_config.model_dump(mode="json"),
            "generation_config": self.generation_config.model_dump(mode="json"),
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }
