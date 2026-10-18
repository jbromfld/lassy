"""Local embedding provider using sentence-transformers."""

import asyncio
import threading
from typing import List
import torch
from sentence_transformers import SentenceTransformer

from config.models import EmbeddingProviderConfig

from ..base import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local embedding provider using sentence-transformers.

    Supports CPU and CUDA devices, with automatic batching and normalization.
    Zero cost, but requires local compute resources.
    """

    def __init__(self, config: EmbeddingProviderConfig):
        """Initialize local embedding provider.

        Args:
            config: Embedding provider configuration
        """
        self.config = config
        self._encode_lock = threading.Lock()
        self.model_name = config.model
        self.dimension = config.dimension
        self.batch_size = config.batch_size
        self.normalize = config.normalize
        self.device = config.device

        # Validate device
        if self.device == "cuda" and not torch.cuda.is_available():
            print(
                f"Warning: CUDA requested but not available. Falling back to CPU."
            )
            self.device = "cpu"

        # Load model
        print(f"Loading embedding model: {self.model_name} on {self.device}...")
        self.model = SentenceTransformer(self.model_name, device=self.device)

        # Verify dimension
        test_embedding = self.model.encode("test", convert_to_numpy=True)
        actual_dimension = len(test_embedding)
        if actual_dimension != self.dimension:
            raise ValueError(
                f"Model {self.model_name} produces {actual_dimension} dimensions; configured {self.dimension}. "
                "Use a matching model configuration and schema or explicitly migrate and re-embed."
            )

        print(f"Embedding model loaded successfully. Dimension: {self.dimension}")

    def _encode(self, *args, **kwargs):
        with self._encode_lock:
            return self.model.encode(*args, **kwargs)

    async def close(self):
        self.model = None
        if self.device == "cuda":
            torch.cuda.empty_cache()

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        # sentence-transformers encode is synchronous, but we wrap in async
        embedding = await asyncio.to_thread(self._encode,
            text,
            convert_to_numpy=True,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
        )
        return embedding.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts.

        Uses batching for efficiency.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        # sentence-transformers handles batching internally
        embeddings = await asyncio.to_thread(self._encode,
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
        )

        return [emb.tolist() for emb in embeddings]

    def get_dimension(self) -> int:
        """Get embedding dimension.

        Returns:
            Embedding dimension
        """
        return self.dimension

    def get_model_name(self) -> str:
        """Get model name.

        Returns:
            Model name string
        """
        return self.model_name

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit.

        Cleanup model if needed.
        """
        # Clear CUDA cache if using GPU
        if self.device == "cuda":
            torch.cuda.empty_cache()
