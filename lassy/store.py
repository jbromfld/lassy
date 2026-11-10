"""PostgreSQL document storage and hybrid retrieval; no query-history writes."""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from .concurrency import BusyError
from .types import Chunk, SearchResult
from .validation import validate_filters


class PgVectorStore:
    def __init__(self, db_pool, dimension=768, lock_timeout=30, connection=None):
        self.db_pool = db_pool
        self.dimension = dimension
        self.lock_timeout = lock_timeout
        self._connection = connection

    @asynccontextmanager
    async def connection(self):
        if self._connection is not None:
            yield self._connection
        else:
            try:
                async with self.db_pool.acquire(timeout=self.lock_timeout) as conn:
                    yield conn
            except TimeoutError:
                raise BusyError("Local database is busy. Retry shortly.") from None

    @asynccontextmanager
    async def document_operation(self, collection, document_id):
        # Session lock spans preparation without keeping a transaction open.
        # The bound store reuses this connection, including with a size-one pool.
        try:
            async with self.db_pool.acquire(timeout=self.lock_timeout) as conn:
                try:
                    async with asyncio.timeout(self.lock_timeout):
                        while not await conn.fetchval(
                            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                            json.dumps([collection, document_id]),
                        ):
                            await asyncio.sleep(0.05)
                    yield PgVectorStore(
                        self.db_pool, self.dimension, self.lock_timeout, conn
                    )
                finally:
                    try:
                        await asyncio.wait_for(
                            conn.execute("SELECT pg_advisory_unlock_all()"), 2
                        )
                    except BaseException:
                        # Never return a connection carrying a session lock to the pool.
                        conn.terminate()
                        raise
        except TimeoutError as exc:
            raise BusyError("This document is being updated. Retry shortly.") from exc

    @staticmethod
    def _chunk(row):
        metadata = row["metadata"]
        vector = row.get("content_vector")
        return Chunk(
            id=row["id"],
            content=row["content"],
            content_hash=row["content_hash"],
            metadata=json.loads(metadata) if isinstance(metadata, str) else metadata,
            vector=[float(v) for v in json.loads(vector)]
            if isinstance(vector, str)
            else vector,
        )

    async def _search(self, query, *args):
        async with self.connection() as conn:
            rows = await conn.fetch(query, *args)
        return [
            SearchResult(self._chunk(row), float(row["score"]), rank)
            for rank, row in enumerate(rows, 1)
        ]

    _projection = "id, content, content_hash, metadata"

    async def vector_search(self, query_vector, top_k=5, filter_dict=None, **kwargs):
        clause, args = self._build_filter_clause(filter_dict, 2)
        return await self._search(
            f"""SELECT {self._projection},
            1 - (content_vector <=> $1::vector) AS score FROM embeddings {clause}
            ORDER BY content_vector <=> $1::vector LIMIT $2""",
            json.dumps(query_vector),
            top_k,
            *args,
        )

    async def keyword_search(self, query_text, top_k=5, filter_dict=None, **kwargs):
        clause, args = self._build_filter_clause(filter_dict, 2)
        return await self._search(
            f"""SELECT {self._projection},
            ts_rank(content_tsvector, query) AS score
            FROM embeddings, plainto_tsquery('english', $1) AS query
            WHERE content_tsvector @@ query {clause.replace("WHERE", "AND")}
            ORDER BY score DESC, id LIMIT $2""",
            query_text,
            top_k,
            *args,
        )

    async def hybrid_search(
        self,
        query_text,
        query_vector,
        top_k=5,
        vector_weight=0.7,
        keyword_weight=0.3,
        filter_dict=None,
        **kwargs,
    ):
        # Fixed equal-weight RRF; branch limits grow with the requested candidate count.
        clause, args = self._build_filter_clause(filter_dict, 5)
        return await self._search(
            f"""
            WITH vector_results AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY content_vector <=> $1::vector) AS rank
                FROM embeddings {clause}
                ORDER BY content_vector <=> $1::vector LIMIT $5
            ), keyword_results AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank(content_tsvector, query) DESC, id) AS rank
                FROM embeddings, plainto_tsquery('english', $2) AS query
                WHERE content_tsvector @@ query {clause.replace("WHERE", "AND")}
                ORDER BY ts_rank(content_tsvector, query) DESC, id LIMIT $5
            ), fused AS (
                SELECT COALESCE(v.id, k.id) AS id,
                    COALESCE(1.0 / ($3 + v.rank), 0) + COALESCE(1.0 / ($3 + k.rank), 0) AS score
                FROM vector_results v FULL OUTER JOIN keyword_results k ON v.id=k.id
                ORDER BY score DESC, id LIMIT $4
            )
            SELECT e.id, content, content_hash,
                metadata,
                score FROM fused JOIN embeddings e ON e.id=fused.id ORDER BY score DESC, e.id
            """,
            json.dumps(query_vector),
            query_text,
            60,
            top_k,
            min(200, max(50, top_k)),
            *args,
        )

    async def get_document_status(self, collection, document_id):
        async with self.connection() as conn:
            row = await conn.fetchrow(
                """SELECT revision, embedding_revision,
                (SELECT count(*) FROM embeddings WHERE metadata->>'collection'=$1
                 AND metadata->>'document_id'=$2) AS chunks
                FROM documents WHERE collection=$1 AND document_id=$2""",
                collection,
                document_id,
            )
            return dict(row) if row else None

    async def replace_document(
        self, collection, document_id, revision, chunks, *, embedding_revision=None
    ):
        """Publish all prepared chunks in a short transaction."""
        async with self.connection() as conn:
            async with conn.transaction():
                await self._publication_lock(conn, collection, document_id)
                current = await conn.fetchval(
                    "SELECT revision FROM documents WHERE collection=$1 AND document_id=$2",
                    collection,
                    document_id,
                )
                if current == revision:
                    return False
                await conn.execute(
                    "DELETE FROM embeddings WHERE metadata->>'collection'=$1 AND metadata->>'document_id'=$2",
                    collection,
                    document_id,
                )
                await conn.executemany(
                    """INSERT INTO embeddings
                    (id, content, content_hash, content_vector, metadata)
                    VALUES ($1,$2,$3,$4::vector,$5::jsonb)""",
                    [
                        (
                            c.id,
                            c.content,
                            c.content_hash,
                            json.dumps(c.vector),
                            json.dumps(c.metadata),
                        )
                        for c in chunks
                    ],
                )
                await conn.execute(
                    """INSERT INTO documents(collection,document_id,revision,embedding_revision)
                    VALUES ($1,$2,$3,$4) ON CONFLICT(collection,document_id) DO UPDATE
                    SET revision=EXCLUDED.revision,embedding_revision=EXCLUDED.embedding_revision,indexed_at=NOW()""",
                    collection,
                    document_id,
                    revision,
                    embedding_revision,
                )
        return True

    @staticmethod
    async def _publication_lock(conn, collection, document_id):
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            json.dumps([collection, document_id]),
        )

    async def update_document_metadata(
        self, collection, document_id, revision, embedding_revision, metadata
    ):
        async with self.connection() as conn:
            async with conn.transaction():
                await self._publication_lock(conn, collection, document_id)
                current = await conn.fetchval(
                    "SELECT embedding_revision FROM documents WHERE collection=$1 AND document_id=$2",
                    collection,
                    document_id,
                )
                if current != embedding_revision:
                    return False
                await conn.execute(
                    """UPDATE embeddings SET metadata=$3::jsonb || jsonb_build_object(
                    'section_ref',metadata->'section_ref','chunk_index',metadata->'chunk_index',
                    'total_chunks',metadata->'total_chunks'), updated_at=NOW()
                    WHERE metadata->>'collection'=$1 AND metadata->>'document_id'=$2""",
                    collection,
                    document_id,
                    json.dumps(metadata),
                )
                await conn.execute(
                    "UPDATE documents SET revision=$3,indexed_at=NOW() WHERE collection=$1 AND document_id=$2",
                    collection,
                    document_id,
                    revision,
                )
        return True

    async def delete_document(self, collection, document_id):
        async with self.document_operation(collection, document_id) as bound:
            async with bound.connection() as conn:
                async with conn.transaction():
                    await self._publication_lock(conn, collection, document_id)
                    await conn.execute(
                        "DELETE FROM embeddings WHERE metadata->>'collection'=$1 AND metadata->>'document_id'=$2",
                        collection,
                        document_id,
                    )
                    await conn.execute(
                        "DELETE FROM documents WHERE collection=$1 AND document_id=$2",
                        collection,
                        document_id,
                    )

    async def count(self, filter_dict: Optional[Dict[str, Any]] = None) -> int:
        """Count chunks in index.

        Args:
            filter_dict: Optional metadata filter

        Returns:
            Number of chunks
        """
        filter_clause, filter_args = self._build_filter_clause(filter_dict)
        query = f"SELECT COUNT(*) FROM embeddings {filter_clause}"

        async with self.connection() as conn:
            return await conn.fetchval(query, *filter_args)

    def _build_filter_clause(self, filter_dict, offset=0):
        """Bind typed JSON values; incompatible stored types simply do not match."""
        conditions, args = [], []
        for key, value in validate_filters(filter_dict or {}).items():
            args.append(key)
            key_param = f"${offset + len(args)}::text"
            value_param = f"${offset + len(args) + 1}"
            if isinstance(value, list):
                args.append([json.dumps(v) for v in value])
                conditions.append(
                    f"metadata->{key_param} = ANY({value_param}::jsonb[])"
                )
            else:
                args.append(json.dumps(value, allow_nan=False))
                conditions.append(
                    f"metadata @> jsonb_build_object({key_param}, {value_param}::jsonb)"
                )
        return ("WHERE " + " AND ".join(conditions) if conditions else "", args)
