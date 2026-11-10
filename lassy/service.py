"""One document service shared by the stdio and HTTP adapters."""

import asyncio
import json
import time
from contextlib import AsyncExitStack, asynccontextmanager

import asyncpg

from .config import load_settings
from .documents import DocumentRequest, upsert_document
from .errors import StartupError
from .models import configure_offline, load_models
from .retrieval import Retriever
from .store import PgVectorStore
from .types import QueryRequest, document_key


async def create_pool(settings):
    return await asyncpg.create_pool(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        min_size=1,
        max_size=settings.db_pool_size,
        timeout=10,
        command_timeout=30,
    )


def index_identity(settings, embedder):
    return {
        "model": settings.local_embedding_model,
        "revision": settings.local_embedding_revision,
        "dimension": settings.local_embedding_dimension,
        "chunking": settings.chunking_config.model_dump(mode="json"),
        "embedding_options": embedder.chunking_identity(),
        "pipeline_version": 2,
    }


async def check_schema(conn, dimension):
    for table in ("embeddings", "documents", "lassy_index_identity"):
        if not await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"):
            raise StartupError(
                "missing_identity_schema"
                if table == "lassy_index_identity"
                else "missing_document_schema"
            )
    try:
        await conn.execute("SELECT embedding_revision FROM documents LIMIT 0")
    except asyncpg.UndefinedColumnError:
        raise StartupError("missing_embedding_revision") from None
    vector_type = await conn.fetchval("""SELECT format_type(atttypid, atttypmod)
        FROM pg_attribute WHERE attrelid='embeddings'::regclass AND attname='content_vector'""")
    if vector_type != f"vector({dimension})":
        raise StartupError("dimension_mismatch")


class Service:
    def __init__(self, settings, pool, embedder, reranker=None):
        self.settings = settings
        self.pool = pool
        self.embedder = embedder
        self.reranker = reranker
        self.store = PgVectorStore(
            pool, settings.pgvector_dimension, settings.document_lock_timeout_seconds
        )

    async def ready(self, initialize=False):
        identity = index_identity(self.settings, self.embedder)
        async with self.pool.acquire(
            timeout=self.settings.document_lock_timeout_seconds
        ) as conn:
            await check_schema(conn, self.settings.local_embedding_dimension)
            if initialize:
                # Fresh indexes bind once. Existing vectors require explicit verified adoption.
                await conn.execute(
                    """INSERT INTO lassy_index_identity(singleton, identity, legacy_unpinned)
                    SELECT TRUE, $1::jsonb, FALSE WHERE NOT EXISTS (SELECT 1 FROM embeddings)
                    ON CONFLICT(singleton) DO NOTHING""",
                    json.dumps(identity),
                )
            row = await conn.fetchrow(
                "SELECT identity, legacy_unpinned FROM lassy_index_identity WHERE singleton"
            )
        if row is None:
            raise StartupError("identity_unverified")
        stored = (
            json.loads(row["identity"])
            if isinstance(row["identity"], str)
            else row["identity"]
        )
        if stored != identity:
            raise StartupError("identity_mismatch")
        if row["legacy_unpinned"]:
            self.embedder.identity_name = self.settings.local_embedding_model

    async def status(self):
        await self.ready()
        return {
            "status": "healthy",
            "embedding_model": self.settings.local_embedding_model,
            "embedding_revision": self.settings.local_embedding_revision,
            "dimension": self.settings.local_embedding_dimension,
            "reranking": self.reranker is not None,
        }

    async def search(self, request):
        request = (
            request
            if isinstance(request, QueryRequest)
            else QueryRequest.model_validate(request)
        )
        start = time.perf_counter()
        retriever = Retriever(
            self.store, self.embedder, self.settings.retrieval_config, self.reranker
        )
        results = await retriever.retrieve(
            request.query,
            top_k=request.top_k or self.settings.retrieval_top_k,
            filters=request.filters,
        )
        chunks = [
            {
                "chunk_id": result.chunk_id,
                "citation": f"[{i}]",
                "content": result.content,
                "title": result.metadata.get("title") or "Unknown",
                "url": str(
                    result.metadata.get("source_url")
                    or result.metadata.get("url")
                    or ""
                ).strip(),
                "score": result.boosted_score,
                "rerank_score": result.rerank_score,
                "section": result.metadata.get("section_ref")
                if result.metadata.get("section_ref") is not None
                else result.metadata.get("chunk_index"),
                **{
                    key: result.metadata.get(key)
                    for key in (
                        "collection",
                        "document_id",
                        "revision",
                        "content_type",
                        "published_at",
                        "last_modified",
                    )
                },
            }
            for i, result in enumerate(results, 1)
        ]
        return {
            "chunks": chunks,
            "metrics": {
                "latency_ms": (time.perf_counter() - start) * 1000,
                "latency_rerank_ms": retriever.rerank_time_ms,
                "chunks_retrieved": len(chunks),
            },
        }

    async def upsert(self, collection, document_id, document):
        document_key(collection, document_id)
        document = (
            document
            if isinstance(document, DocumentRequest)
            else DocumentRequest.model_validate(document)
        )
        return await upsert_document(
            self.store,
            self.embedder,
            self.settings.chunking_config,
            collection,
            document_id,
            document,
        )

    async def delete(self, collection, document_id):
        document_key(collection, document_id)
        await self.store.delete_document(collection, document_id)
        return {"deleted": True, "collection": collection, "document_id": document_id}


async def close_pool(pool):
    try:
        await asyncio.wait_for(pool.close(), 10)
    except BaseException:
        pool.terminate()
        raise


@asynccontextmanager
async def open_service(settings=None):
    configure_offline()
    settings = settings or load_settings()
    async with AsyncExitStack() as cleanup:
        pool = await create_pool(settings)
        cleanup.push_async_callback(close_pool, pool)
        embedder, reranker = await load_models(settings)
        cleanup.push_async_callback(embedder.close)
        if reranker:
            cleanup.push_async_callback(reranker.close)
        service = Service(settings, pool, embedder, reranker)
        try:
            await service.ready(initialize=True)
        except asyncpg.InsufficientPrivilegeError:
            raise StartupError("database_permissions") from None
        yield service


async def adopt_index(settings, legacy_unpinned=False):
    """Operator attests the prepared snapshot matches existing vectors; never rewrites vectors."""
    async with AsyncExitStack() as cleanup:
        pool = await create_pool(settings)
        cleanup.push_async_callback(close_pool, pool)
        embedder, reranker = await load_models(settings)
        cleanup.push_async_callback(embedder.close)
        if reranker:
            cleanup.push_async_callback(reranker.close)
        identity = index_identity(settings, embedder)
        async with pool.acquire() as conn, conn.transaction():
            await check_schema(conn, settings.local_embedding_dimension)
            # Run during a maintenance window; prohibit concurrent data publication.
            await conn.execute(
                "LOCK TABLE embeddings, documents, lassy_index_identity IN ACCESS EXCLUSIVE MODE"
            )
            await conn.execute(
                """INSERT INTO lassy_index_identity(singleton, identity, legacy_unpinned)
                VALUES (TRUE, $1::jsonb, $2)""",
                json.dumps(identity),
                legacy_unpinned,
            )
