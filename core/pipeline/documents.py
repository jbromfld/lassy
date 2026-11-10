"""Shared text ingestion with bounded preparation and atomic publication."""
import hashlib
import json
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, field_validator, model_validator

from core.concurrency import document_operation
from core.ingestion.chunker import DocumentChunker
from core.providers.base import Chunk
from core.validation import validate_metadata

MAX_DOCUMENT_CHARS = 2_000_000
MAX_CHUNKS = 10_000
PIPELINE_VERSION = 2


class DocumentSection(BaseModel):
    ref: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)

    @field_validator('text', 'ref')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('section text and reference must not be blank')
        return value


class DocumentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)
    title: str = Field(default='Untitled', max_length=2000)
    url: str | None = Field(default=None, max_length=4000)
    published_at: datetime | None = None
    content_type: str = Field(default='text', min_length=1, max_length=200)
    metadata: dict = Field(default_factory=dict)
    sections: list[DocumentSection] = Field(default_factory=list, max_length=2000)

    @field_validator('metadata')
    @classmethod
    def bounded_metadata(cls, value):
        return validate_metadata(value)

    @model_validator(mode='after')
    def bounded_text(self):
        if not self.content.strip():
            raise ValueError('content must not be blank')
        if sum(len(s.text) for s in self.sections) > MAX_DOCUMENT_CHARS:
            raise ValueError(f'Section text must total at most {MAX_DOCUMENT_CHARS} characters')
        return self


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _prepare(document, config, embedder, metadata):
    chunks = []
    chunker = DocumentChunker(config)
    for section in document.sections or [None]:
        for part in chunker.chunk_document(section.text if section else document.content):
            for text in embedder.split_text(part.content):
                if text.strip():
                    chunks.append((text, {**metadata, 'section_ref': section.ref if section else None}))
                    if len(chunks) > MAX_CHUNKS:
                        raise ValueError(f'Document produces more than {MAX_CHUNKS} chunks')
    if not chunks:
        raise ValueError('Content produced no chunks; provide nonblank text')
    return chunks


async def upsert_document(store, embedder, chunking_config, collection, document_id, document):
    async with document_operation(collection, document_id):
        return await _upsert_document(store, embedder, chunking_config, collection, document_id, document)


async def _upsert_document(store, embedder, chunking_config, collection, document_id, document):
    # Crawl timestamps are observations, not source changes.
    payload = document.model_dump(mode='json')
    payload['metadata'].pop('scraped_at', None)
    embedding_revision = _digest({
        'text': payload['sections'] or payload['content'],
        'chunking': chunking_config.model_dump(mode='json'),
        'model': embedder.get_model_name(), 'dimension': embedder.get_dimension(),
        'embedding_options': embedder.chunking_identity(), 'pipeline_version': PIPELINE_VERSION,
    })
    revision = _digest({'document': payload, 'embedding_revision': embedding_revision})
    current = await store.get_document_status(collection, document_id)
    if current and current['revision'] == revision:
        return _result(collection, document_id, revision, current['chunks'], 0, False)
    metadata = {**payload['metadata'], 'collection': collection, 'document_id': document_id,
                'title': document.title, 'source_url': document.url or '',
                'published_at': payload['published_at'], 'content_type': document.content_type,
                'revision': revision}
    if current and current.get('embedding_revision') == embedding_revision:
        if await store.update_document_metadata(collection, document_id, revision, embedding_revision, metadata):
            return _result(collection, document_id, revision, current['chunks'], 0, True)
    prepared = await embedder.prepare_document(_prepare, document, chunking_config, embedder, metadata)
    vectors = await embedder.embed_batch([text for text, _ in prepared])
    if len(vectors) != len(prepared):
        raise RuntimeError('Embedding provider returned an incorrect number of vectors')
    chunks = []
    for i, ((text, provenance), vector) in enumerate(zip(prepared, vectors)):
        identity = json.dumps([collection, document_id, embedding_revision, i])
        chunks.append(Chunk(uuid5(NAMESPACE_URL, identity), text,
            hashlib.sha256(identity.encode()).hexdigest(), vector,
            {**provenance, 'chunk_index': i, 'total_chunks': len(prepared)}))
    changed = await store.replace_document(collection, document_id, revision, chunks,
                                           embedding_revision=embedding_revision)
    return _result(collection, document_id, revision, len(chunks), len(vectors), changed)


def _result(collection, document_id, revision, chunks, embeddings, changed):
    return {'collection': collection, 'document_id': document_id, 'revision': revision,
            'chunks_created': chunks, 'embeddings_generated': embeddings, 'changed': changed}
