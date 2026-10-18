"""Shared text document ingestion; clients never produce retrieval chunks/vectors."""
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from uuid import NAMESPACE_URL, uuid5

from core.ingestion.chunker import DocumentChunker
from core.providers.base import Chunk


class DocumentSection(BaseModel):
    ref: str = Field(min_length=1)
    text: str = Field(min_length=1)

    @field_validator("text", "ref")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("section text and reference must not be blank")
        return value


class DocumentRequest(BaseModel):
    content: str = Field(min_length=1)
    title: str = "Untitled"
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    content_type: str = "text"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    sections: List[DocumentSection] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value):
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


async def upsert_document(store, embedder, chunking_config, collection, document_id, document):
    # Include pipeline configuration so backfill after model/chunker changes reindexes.
    payload = document.model_dump(mode="json")
    revision = hashlib.sha256(json.dumps({
        "document": payload, "chunking": chunking_config.model_dump(mode="json"),
        "model": embedder.get_model_name(), "dimension": embedder.get_dimension(),
        "pipeline_version": 1,
    }, sort_keys=True).encode()).hexdigest()
    current = await store.get_document_status(collection, document_id)
    if current and current["revision"] == revision:
        return {"collection": collection, "document_id": document_id, "revision": revision,
                "chunks_created": current["chunks"], "embeddings_generated": 0, "changed": False}
    metadata = {**document.metadata, "collection": collection, "document_id": document_id,
                "title": document.title, "source_url": document.url or "",
                "published_at": document.published_at.isoformat() if document.published_at else None,
                "content_type": document.content_type, "revision": revision}
    chunker = DocumentChunker(chunking_config)
    parts = document.sections or [None]
    prepared = []
    for section in parts:
        text = section.text if section else document.content
        section_metadata = {**metadata, "section_ref": section.ref if section else None}
        prepared.extend(chunker.chunk_document(text, section_metadata))
    if not prepared:
        raise ValueError("Content produced no chunks; provide nonblank text")
    vectors = await embedder.embed_batch([c.content for c in prepared])
    if len(vectors) != len(prepared):
        raise RuntimeError("Embedding provider returned an incorrect number of vectors")
    chunks = []
    for i, (part, vector) in enumerate(zip(prepared, vectors)):
        # Scope identity to document and position, preserving duplicate text's provenance.
        identity = json.dumps([collection, document_id, revision, i])
        chunks.append(Chunk(uuid5(NAMESPACE_URL, identity), part.content,
            hashlib.sha256(identity.encode()).hexdigest(), vector,
            {**part.metadata, "chunk_index": i, "total_chunks": len(prepared)}))
    changed = await store.replace_document(collection, document_id, revision, chunks)
    return {"collection": collection, "document_id": document_id, "revision": revision,
            "chunks_created": len(chunks), "embeddings_generated": len(vectors), "changed": changed}
