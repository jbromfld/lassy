"""Document chunking strategies."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from config.models import ChunkingConfig, ChunkStrategy


@dataclass
class ChunkResult:
    """Result of document chunking."""

    id: UUID
    content: str
    content_hash: str
    word_count: int
    char_count: int
    metadata: Dict[str, Any]


class DocumentChunker:
    """Document chunker with multiple strategies.

    Implements fixed-size and recursive chunking strategies
    as defined in DESIGN_DECISIONS.md.
    """

    def __init__(self, config: ChunkingConfig):
        """Initialize document chunker.

        Args:
            config: Chunking configuration
        """
        self.config = config
        self.strategy = config.strategy
        self.chunk_size = config.chunk_size  # in words
        self.chunk_overlap = config.chunk_overlap  # in words
        self.min_chunk_size = config.min_chunk_size
        self.max_chunk_size = config.max_chunk_size
        self.separators = config.separators
        self.keep_separator = config.keep_separator

    def chunk_document(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ChunkResult]:
        """Chunk document using configured strategy.

        Args:
            content: Document content to chunk
            metadata: Optional metadata to attach to chunks

        Returns:
            List of ChunkResult objects
        """
        if not content or not content.strip():
            return []

        # Clean content
        content = self._clean_content(content)

        # Choose chunking strategy
        if self.strategy == ChunkStrategy.FIXED:
            chunks = self._chunk_fixed(content)
        elif self.strategy == ChunkStrategy.RECURSIVE:
            chunks = self._chunk_recursive(content)
        elif self.strategy == ChunkStrategy.SEMANTIC:
            # Semantic chunking not implemented yet, fall back to recursive
            chunks = self._chunk_recursive(content)
        else:
            raise ValueError(f"Unknown chunking strategy: {self.strategy}")

        if not chunks:
            # A valid short summary/section is still a searchable document.
            chunks = [content]

        # Convert to ChunkResult objects
        results = []
        for i, chunk_text in enumerate(chunks):
            chunk_metadata = metadata.copy() if metadata else {}
            chunk_metadata["chunk_index"] = i
            chunk_metadata["total_chunks"] = len(chunks)

            result = ChunkResult(
                id=uuid4(),
                content=chunk_text,
                content_hash=self._hash_content(chunk_text),
                word_count=len(chunk_text.split()),
                char_count=len(chunk_text),
                metadata=chunk_metadata,
            )
            results.append(result)

        return results

    def _chunk_fixed(self, content: str) -> List[str]:
        """Fixed-size chunking strategy.

        Splits text into fixed-size chunks based on word count,
        with specified overlap.

        Args:
            content: Text to chunk

        Returns:
            List of chunk strings
        """
        words = content.split()
        chunks = []

        if len(words) <= self.chunk_size:
            # Content fits in one chunk
            return [content]

        # Calculate step size (chunk_size - overlap)
        step = max(1, self.chunk_size - self.chunk_overlap)

        i = 0
        while i < len(words):
            # Get chunk
            chunk_words = words[i : i + self.chunk_size]

            # Skip if chunk is too small (unless it's the last one)
            if len(chunk_words) < self.min_chunk_size and i + self.chunk_size < len(
                words
            ):
                i += step
                continue

            chunk_text = " ".join(chunk_words)
            chunks.append(chunk_text)

            # Move to next chunk
            i += step

        return chunks

    def _chunk_recursive(self, content: str) -> List[str]:
        """Recursive chunking strategy.

        Attempts to split text at natural boundaries (paragraphs, sentences)
        while respecting size constraints.

        Args:
            content: Text to chunk

        Returns:
            List of chunk strings
        """
        return self._recursive_split(content, self.separators.copy())

    def _recursive_split(self, text: str, separators: List[str]) -> List[str]:
        """Recursively split text using separators.

        Args:
            text: Text to split
            separators: List of separators (ordered by priority)

        Returns:
            List of chunks
        """
        if not separators:
            # No more separators, use fixed chunking
            return self._chunk_fixed(text)

        # Try current separator
        separator = separators[0]
        remaining_separators = separators[1:]

        # Split by current separator
        if separator:
            splits = text.split(separator)

            # Keep separator if configured
            if self.keep_separator and separator != " ":
                # Re-add separator to splits (except last one)
                splits = [
                    split + separator if i < len(splits) - 1 else split
                    for i, split in enumerate(splits)
                ]
        else:
            # Empty separator means split by characters
            splits = list(text)

        # Merge splits into chunks
        chunks = []
        current_chunk = []
        current_word_count = 0

        for split in splits:
            split = split.strip()
            if not split:
                continue

            split_word_count = len(split.split())

            # Check if adding this split would exceed chunk size
            if current_word_count + split_word_count > self.chunk_size and current_chunk:
                # Save current chunk
                chunk_text = " ".join(current_chunk)
                if len(chunk_text.split()) >= self.min_chunk_size:
                    chunks.append(chunk_text)

                # Start new chunk with overlap
                if self.chunk_overlap > 0 and current_chunk:
                    # Keep last N words for overlap
                    overlap_text = " ".join(current_chunk)
                    overlap_words = overlap_text.split()[-self.chunk_overlap :]
                    current_chunk = overlap_words
                    current_word_count = len(current_chunk)
                else:
                    current_chunk = []
                    current_word_count = 0

            # Add split to current chunk
            current_chunk.append(split)
            current_word_count += split_word_count

            # Check if current chunk exceeds max size
            if current_word_count > self.max_chunk_size:
                # Split is too large, need to recursively split it
                if remaining_separators:
                    sub_chunks = self._recursive_split(split, remaining_separators)
                    for sub_chunk in sub_chunks:
                        if len(sub_chunk.split()) >= self.min_chunk_size:
                            chunks.append(sub_chunk)
                else:
                    # No more separators, use fixed chunking
                    sub_chunks = self._chunk_fixed(split)
                    chunks.extend(sub_chunks)

                # Reset current chunk
                current_chunk = []
                current_word_count = 0

        # Add final chunk
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            if len(chunk_text.split()) >= self.min_chunk_size:
                chunks.append(chunk_text)

        return chunks

    def _clean_content(self, content: str) -> str:
        """Clean document content.

        Args:
            content: Raw content

        Returns:
            Cleaned content
        """
        # Remove excessive whitespace
        content = re.sub(r"\s+", " ", content)

        # Remove excessive newlines
        content = re.sub(r"\n{3,}", "\n\n", content)

        # Trim
        content = content.strip()

        return content

    def _hash_content(self, content: str) -> str:
        """Generate hash for content (for deduplication).

        Args:
            content: Content to hash

        Returns:
            SHA-256 hash (hex string)
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
