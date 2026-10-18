"""Base abstract classes for all providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID


# ============================================
# Data Models
# ============================================


@dataclass
class Chunk:
    """Document chunk with content and metadata."""

    id: UUID
    content: str
    content_hash: str
    vector: Optional[List[float]] = None
    metadata: Optional[Dict[str, Any]] = None
    score: Optional[float] = None


@dataclass
class SearchResult:
    """Search result with chunk and score."""

    chunk: Chunk
    score: float
    rank: int


# ============================================
# Embedding Provider
# ============================================


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        pass

    @abstractmethod
    def get_dimension(self) -> int:
        """Get embedding dimension.

        Returns:
            Embedding dimension (e.g., 768, 1536)
        """
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Get model name.

        Returns:
            Model name string
        """
        pass

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        pass


class VectorStore(ABC):
    """Abstract base class for vector stores."""

    @abstractmethod
    async def create_index(
        self,
        index_name: str,
        dimension: int,
        **kwargs,
    ) -> None:
        """Create vector index.

        Args:
            index_name: Name of the index
            dimension: Vector dimension
            **kwargs: Additional provider-specific parameters
        """
        pass

    @abstractmethod
    async def index_exists(self, index_name: str) -> bool:
        """Check if index exists.

        Args:
            index_name: Name of the index

        Returns:
            True if index exists
        """
        pass

    @abstractmethod
    async def insert_chunk(self, chunk: Chunk) -> None:
        """Insert a single chunk.

        Args:
            chunk: Chunk to insert
        """
        pass

    @abstractmethod
    async def insert_chunks(self, chunks: List[Chunk]) -> int:
        """Insert multiple chunks.

        Args:
            chunks: List of chunks to insert

        Returns:
            Number of chunks inserted
        """
        pass

    @abstractmethod
    async def vector_search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_dict: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Perform vector similarity search.

        Args:
            query_vector: Query embedding vector
            top_k: Number of results to return
            filter_dict: Optional metadata filters
            **kwargs: Additional provider-specific parameters

        Returns:
            List of search results
        """
        pass

    @abstractmethod
    async def keyword_search(
        self,
        query_text: str,
        top_k: int = 5,
        filter_dict: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Perform keyword search.

        Args:
            query_text: Query text
            top_k: Number of results to return
            filter_dict: Optional metadata filters
            **kwargs: Additional provider-specific parameters

        Returns:
            List of search results
        """
        pass

    @abstractmethod
    async def hybrid_search(
        self,
        query_text: str,
        query_vector: List[float],
        top_k: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
        filter_dict: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Perform hybrid search (vector + keyword).

        Args:
            query_text: Query text for keyword search
            query_vector: Query embedding for vector search
            top_k: Number of results to return
            vector_weight: Weight for vector search (0.0-1.0)
            keyword_weight: Weight for keyword search (0.0-1.0)
            filter_dict: Optional metadata filters
            **kwargs: Additional provider-specific parameters

        Returns:
            List of search results ranked by combined score
        """
        pass

    @abstractmethod
    async def get_chunk(self, chunk_id: UUID) -> Optional[Chunk]:
        """Get chunk by ID.

        Args:
            chunk_id: Chunk UUID

        Returns:
            Chunk if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_chunk_metadata(
        self, chunk_id: UUID, metadata: Dict[str, Any]
    ) -> None:
        """Update chunk metadata.

        Args:
            chunk_id: Chunk UUID
            metadata: Metadata to update
        """
        pass

    @abstractmethod
    async def delete_chunk(self, chunk_id: UUID) -> None:
        """Delete chunk by ID.

        Args:
            chunk_id: Chunk UUID
        """
        pass

    @abstractmethod
    async def delete_by_filter(self, filter_dict: Dict[str, Any]) -> int:
        """Delete chunks matching filter.

        Args:
            filter_dict: Metadata filter

        Returns:
            Number of chunks deleted
        """
        pass

    @abstractmethod
    async def count(self, filter_dict: Optional[Dict[str, Any]] = None) -> int:
        """Count chunks in index.

        Args:
            filter_dict: Optional metadata filter

        Returns:
            Number of chunks
        """
        pass

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        pass
