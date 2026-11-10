"""Word-based document chunking that retains paragraphs and short endings."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from .config import ChunkingConfig, ChunkStrategy


@dataclass
class ChunkResult:
    id: UUID
    content: str
    content_hash: str
    word_count: int
    char_count: int
    metadata: dict[str, Any]


class DocumentChunker:
    def __init__(self, config: ChunkingConfig):
        self.config = config

    def chunk_document(self, content, metadata=None):
        content = self._clean_content(content)
        if not content:
            return []
        if self.config.strategy == ChunkStrategy.FIXED:
            chunks = self._chunk_fixed(content)
        else:
            # Retain the existing semantic alias; it still uses recursive splitting.
            chunks = self._merge(self._units(content, self.config.separators))
        if len(chunks) > 1 and len(chunks[-1].split()) < self.config.min_chunk_size:
            merged = self._join_overlap(chunks[-2], chunks[-1])
            if len(merged.split()) <= self.config.max_chunk_size:
                chunks[-2:] = [merged]
        return [
            ChunkResult(
                uuid4(),
                text,
                hashlib.sha256(text.encode()).hexdigest(),
                len(text.split()),
                len(text),
                {**(metadata or {}), "chunk_index": i, "total_chunks": len(chunks)},
            )
            for i, text in enumerate(chunks)
        ]

    def _chunk_fixed(self, text):
        words = list(re.finditer(r"\S+", text))
        chunks = []
        step = self.config.chunk_size - self.config.chunk_overlap
        for start in range(0, len(words), step):
            end = min(start + self.config.chunk_size, len(words))
            chunks.append(text[words[start].start() : words[end - 1].end()])
            if end == len(words):
                break
        return chunks

    def _units(self, text, separators):
        if len(text.split()) <= self.config.chunk_size:
            return [text]
        if not separators:
            # Overlap is added at merge time, not twice at recursive boundaries.
            words = list(re.finditer(r"\S+", text))
            return [
                text[
                    words[i].start() : words[
                        min(i + self.config.chunk_size, len(words)) - 1
                    ].end()
                ]
                for i in range(0, len(words), self.config.chunk_size)
            ]
        separator, *remaining = separators
        if not separator or separator not in text:
            return self._units(text, remaining)
        parts = text.split(separator)
        units = []
        for i, part in enumerate(parts):
            if i < len(parts) - 1 and self.config.keep_separator:
                part += separator
            if part.strip():
                units.extend(self._units(part, remaining))
        return units

    def _merge(self, units):
        chunks, current = [], ""
        for unit in units:
            joined = self._join(current, unit)
            if current and len(joined.split()) > self.config.chunk_size:
                chunks.append(current.strip())
                # Only add as much overlap as fits alongside the next unit.
                room = max(0, self.config.chunk_size - len(unit.split()))
                overlap = min(self.config.chunk_overlap, room)
                current = " ".join(current.split()[-overlap:]) + " " if overlap else ""
                joined = self._join(current, unit)
            current = joined
        if current.strip():
            chunks.append(current.strip())
        return chunks

    @staticmethod
    def _join(left, right):
        if not left or not right or left[-1].isspace() or right[0].isspace():
            return left + right
        return left + " " + right

    def _join_overlap(self, left, right):
        left_words, right_words = left.split(), list(re.finditer(r"\S+", right))
        for count in range(
            min(self.config.chunk_overlap, len(left_words), len(right_words)), 0, -1
        ):
            if left_words[-count:] == [word.group() for word in right_words[:count]]:
                right = right[right_words[count - 1].end() :]
                break
        return self._join(left, right)

    @staticmethod
    def _clean_content(content):
        content = re.sub(
            r"[^\S\n]+", " ", content.replace("\r\n", "\n").replace("\r", "\n")
        )
        return re.sub(r"\n{3,}", "\n\n", content).strip()
