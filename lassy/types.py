"""Document values and the shared bounded request contract."""

import asyncio
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .validation import validate_filters


@dataclass
class Chunk:
    id: UUID
    content: str
    content_hash: str
    vector: list[float] | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class SearchResult:
    chunk: Chunk
    score: float
    rank: int


class EmbeddingProvider:
    """Shared text-preparation behavior, also useful for deterministic test encoders."""

    async def prepare_document(self, function, *args):
        return await asyncio.to_thread(function, *args)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=100)
    filters: dict | None = None
    retrieve_only: Literal[True] = True

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value):
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()

    @field_validator("filters")
    @classmethod
    def bounded_filters(cls, value):
        return validate_filters(value) if value is not None else value


def document_key(collection, document_id):
    for value, limit in ((collection, 200), (document_id, 500)):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > limit
        ):
            raise ValueError("Invalid collection or document ID")
    return collection, document_id
