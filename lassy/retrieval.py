"""Retrieval system with metadata boosting."""

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import RetrievalConfig
from .store import PgVectorStore
from .types import EmbeddingProvider, SearchResult


@dataclass
class RetrievalResult:
    """Enhanced retrieval result with boosted score."""

    chunk_id: str
    content: str
    score: float  # Base score
    boosted_score: float  # Score after metadata boosting
    rank: int
    metadata: Dict[str, Any]

    # Boost breakdown for debugging
    recency_boost: float = 1.0
    rerank_score: Optional[float] = None


class Retriever:
    """Retrieval system with metadata boosting.

    Combines local vector/keyword retrieval, recency, and optional reranking.
    """

    def __init__(
        self,
        vector_store: PgVectorStore,
        embedding_provider: EmbeddingProvider,
        config: RetrievalConfig,
        reranker=None,
    ):
        """Initialize retriever.

        Args:
            vector_store: Vector store instance
            embedding_provider: Embedding provider instance
            config: Retrieval configuration
        """
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.config = config
        self.reranker = reranker
        self.rerank_time_ms = 0.0

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievalResult]:
        """Retrieve relevant chunks for query with metadata boosting.

        Args:
            query: Query text
            top_k: Number of results (defaults to config.top_k)
            filters: Optional metadata filters

        Returns:
            List of RetrievalResult objects, sorted by boosted score
        """
        top_k = top_k or self.config.top_k

        candidate_k = (
            max(top_k, self.config.rerank_top_k)
            if self.config.enable_reranking
            else top_k
        )

        # Generate query embedding
        query_vector = await self.embedding_provider.embed_text(query)

        # Perform search
        if self.config.hybrid_search:
            # Hybrid search (vector + keyword)
            search_results = await self.vector_store.hybrid_search(
                query_text=query,
                query_vector=query_vector,
                top_k=candidate_k * 2,  # Get more results for boosting
                filter_dict=filters,
            )
        else:
            # Pure vector search
            search_results = await self.vector_store.vector_search(
                query_vector=query_vector,
                top_k=candidate_k * 2,
                filter_dict=filters,
            )

        # Apply metadata boosting
        boosted_results = self._apply_metadata_boosting(search_results)

        # Filter by relevance threshold
        # Note: For hybrid search with RRF, scores are typically very small (0.01-0.05)
        # Only apply threshold for pure vector search or when threshold is reasonable
        if self.config.relevance_threshold and not self.config.hybrid_search:
            boosted_results = [
                r
                for r in boosted_results
                if r.boosted_score >= self.config.relevance_threshold
            ]
        elif self.config.relevance_threshold and self.config.hybrid_search:
            # For hybrid search with RRF, use a much lower threshold
            # or skip threshold filtering entirely
            pass  # Skip relevance filtering for hybrid search with RRF

        # Re-rank by boosted score
        boosted_results.sort(key=lambda x: x.boosted_score, reverse=True)

        if self.config.enable_reranking:
            rerank_start = time.perf_counter()
            if self.reranker is None:
                raise RuntimeError("Reranking enabled without a configured reranker")
            candidates = boosted_results[:candidate_k]
            scores = await self.reranker.score(query, [r.content for r in candidates])
            if len(scores) != len(candidates):
                raise RuntimeError("Reranker returned an incorrect number of scores")
            for result, score in zip(candidates, scores):
                if not math.isfinite(score):
                    raise ValueError("Reranker returned a nonfinite score")
                result.rerank_score = score
            candidates.sort(key=lambda r: r.rerank_score, reverse=True)
            boosted_results = candidates
            self.rerank_time_ms = (time.perf_counter() - rerank_start) * 1000

        # Take top K
        boosted_results = boosted_results[:top_k]

        # Update ranks
        for rank, result in enumerate(boosted_results, start=1):
            result.rank = rank

        return boosted_results

    def _apply_metadata_boosting(
        self, search_results: List[SearchResult]
    ) -> List[RetrievalResult]:
        """Apply metadata boosting to search results.

        Implements boosting based on:
        - Recency (last_modified date)

        Args:
            search_results: Raw search results

        Returns:
            List of RetrievalResult with boosted scores
        """
        now = datetime.now(timezone.utc)
        results = []

        for search_result in search_results:
            chunk = search_result.chunk
            base_score = search_result.score
            metadata = chunk.metadata or {}

            # Calculate individual boosts
            recency_boost = self._calculate_recency_boost(metadata, now)

            # Calculate final boosted score
            boosted_score = base_score * recency_boost

            result = RetrievalResult(
                chunk_id=str(chunk.id),
                content=chunk.content,
                score=base_score,
                boosted_score=boosted_score,
                rank=search_result.rank,
                metadata=metadata,
                recency_boost=recency_boost,
            )
            results.append(result)

        return results

    def _calculate_recency_boost(
        self, metadata: Dict[str, Any], now: datetime
    ) -> float:
        """Prefer publication time, falling back to modification time; decay toward neutral."""
        if not self.config.recency_boost_enabled:
            return 1.0
        for key in ("published_at", "last_modified"):
            value = metadata.get(key)
            if not value:
                continue
            try:
                date = (
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if isinstance(value, str)
                    else value
                )
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                age = (now - date).total_seconds() / 86400
                return 1.0 if age < 0 else 1.0 + 0.5 * math.exp(-age / 180)
            except (ValueError, TypeError, AttributeError):
                continue
        return 1.0
