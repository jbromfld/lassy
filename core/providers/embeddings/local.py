"""Local embedding provider using sentence-transformers."""

from typing import List
import torch
from sentence_transformers import SentenceTransformer

from config.models import EmbeddingProviderConfig
from core.concurrency import InferenceGate

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
        self.gate = InferenceGate(config.queue_timeout_seconds, config.max_pending)
        self.model_name = config.model
        self.revision = config.revision
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
        self.model = SentenceTransformer(self.model_name, device=self.device,
                                         **({"revision": config.revision} if config.revision else {}))

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
        return self.model.encode(*args, **kwargs)

    async def close(self):
        await self.gate.close()
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
        embedding = await self.gate.run(self._encode,
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

        result = []
        for start in range(0, len(texts), self.batch_size):
            embeddings = await self.gate.run(self._encode,
                texts[start:start+self.batch_size], batch_size=self.batch_size,
                convert_to_numpy=True, normalize_embeddings=self.normalize,
                show_progress_bar=False)
            result.extend(emb.tolist() for emb in embeddings)
        return result

    def chunking_identity(self):
        return {'max_sequence_length': self.model.max_seq_length, 'normalize': self.normalize}

    async def prepare_document(self, function, *args):
        # Hugging Face tokenizers can mutate truncation state during encode.
        # Share admission with inference rather than tokenizing concurrently.
        return await self.gate.run(function, *args)

    def split_text(self, text):
        """Retain the original characters, splitting only when tokens exceed the model limit."""
        limit = self.model.max_seq_length

        def fits(value):
            return len(self.model.tokenizer.encode(value, add_special_tokens=True,
                       truncation=False, verbose=False)) <= limit

        pieces = []
        while text:
            if fits(text):
                pieces.append(text)
                break
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if fits(text[:middle]):
                    low = middle
                else:
                    high = middle - 1
            if not low:
                raise ValueError('Embedding token limit cannot fit a single character')
            # Prefer a word boundary, then verify the actual emitted piece.
            boundary = text.rfind(' ', 0, low + 1)
            end = boundary if boundary > low // 2 else low
            while end and not fits(text[:end]):
                end -= 1
            if not end:
                raise ValueError('Embedding token limit cannot fit source text')
            pieces.append(text[:end])
            text = text[end:]
        return pieces

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
        return f"{self.model_name}@{self.revision}" if self.revision else self.model_name

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
