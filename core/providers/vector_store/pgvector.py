"""PostgreSQL pgvector implementation of VectorStore."""

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from asyncpg import Pool

from config.models import VectorStoreConfig

from ..base import Chunk, SearchResult, VectorStore


class PgVectorStore(VectorStore):
    """PostgreSQL pgvector vector store with hybrid search."""

    def __init__(self, config: VectorStoreConfig, db_pool: Pool):
        """Initialize pgvector store.

        Args:
            config: Vector store configuration
            db_pool: Database connection pool
        """
        self.config = config
        self.db_pool = db_pool
        self.dimension = config.dimension

    async def create_index(
        self,
        index_name: str,
        dimension: int,
        **kwargs,
    ) -> None:
        """Create vector index.

        Note: For pgvector, indexes are created at table creation time.
        This method ensures the embeddings table exists with proper indexes.

        Args:
            index_name: Name of the index (not used for pgvector, table is 'embeddings')
            dimension: Vector dimension
            **kwargs: Additional parameters
        """
        # Index creation is handled by init.sql
        # This method is a no-op for pgvector since indexes are pre-created
        pass

    async def index_exists(self, index_name: str) -> bool:
        """Check if index exists.

        Args:
            index_name: Name of the index

        Returns:
            True (embeddings table always exists after init)
        """
        async with self.db_pool.acquire() as conn:
            query = """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'embeddings'
                )
            """
            return await conn.fetchval(query)

    async def insert_chunk(self, chunk: Chunk) -> None:
        """Insert a single chunk.

        Args:
            chunk: Chunk to insert
        """
        await self.insert_chunks([chunk])

    async def insert_chunks(self, chunks: List[Chunk]) -> int:
        """Insert multiple chunks.

        Uses ON CONFLICT to handle duplicates (same content_hash).

        Args:
            chunks: List of chunks to insert

        Returns:
            Number of chunks inserted
        """
        if not chunks:
            return 0

        async with self.db_pool.acquire() as conn:
            # Prepare data for batch insert
            records = []
            for chunk in chunks:
                # Convert vector to pgvector format string
                vector_str = "[" + ",".join(map(str, chunk.vector)) + "]"

                records.append(
                    (
                        chunk.id,
                        chunk.content,
                        chunk.content_hash,
                        vector_str,
                        json.dumps(chunk.metadata) if chunk.metadata else "{}",
                    )
                )

            # Batch insert with ON CONFLICT
            query = """
                INSERT INTO embeddings (
                    id, content, content_hash, content_vector, metadata
                ) VALUES ($1, $2, $3, $4::vector, $5::jsonb)
                ON CONFLICT (content_hash) DO UPDATE SET
                    content = EXCLUDED.content,
                    content_vector = EXCLUDED.content_vector,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
            """

            await conn.executemany(query, records)
            return len(chunks)

    async def vector_search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_dict: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Perform vector similarity search using cosine distance.

        Args:
            query_vector: Query embedding vector
            top_k: Number of results to return
            filter_dict: Optional metadata filters
            **kwargs: Additional parameters

        Returns:
            List of search results ordered by similarity
        """
        # Convert query vector to pgvector format
        vector_str = "[" + ",".join(map(str, query_vector)) + "]"

        # Build filter clause
        filter_clause, filter_args = self._build_filter_clause(filter_dict, 2)

        query = f"""
            SELECT
                id,
                content,
                content_hash,
                content_vector,
                metadata || jsonb_build_object('avg_feedback_score', avg_feedback_score,
                    'num_feedback', num_feedback, 'retrieval_count', retrieval_count) AS metadata,
                1 - (content_vector <=> $1::vector) AS score
            FROM embeddings
            {filter_clause}
            ORDER BY content_vector <=> $1::vector
            LIMIT $2
        """

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, vector_str, top_k, *filter_args)

            results = []
            for rank, row in enumerate(rows, start=1):
                chunk = Chunk(
                    id=row["id"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                    vector=list(row["content_vector"]) if row["content_vector"] else None,
                    metadata=row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"]) if row["metadata"] else {},
                    score=float(row["score"]),
                )
                results.append(SearchResult(chunk=chunk, score=float(row["score"]), rank=rank))

            return results

    async def keyword_search(
        self,
        query_text: str,
        top_k: int = 5,
        filter_dict: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Perform keyword search using PostgreSQL full-text search (tsvector).

        Args:
            query_text: Query text
            top_k: Number of results to return
            filter_dict: Optional metadata filters
            **kwargs: Additional parameters

        Returns:
            List of search results ordered by relevance
        """
        # Build filter clause
        filter_clause, filter_args = self._build_filter_clause(filter_dict, 2)

        query = f"""
            SELECT
                id,
                content,
                content_hash,
                content_vector,
                metadata || jsonb_build_object('avg_feedback_score', avg_feedback_score,
                    'num_feedback', num_feedback, 'retrieval_count', retrieval_count) AS metadata,
                ts_rank(content_tsvector, query) AS score
            FROM embeddings, plainto_tsquery('english', $1) AS query
            WHERE content_tsvector @@ query
            {filter_clause.replace('WHERE', 'AND')}
            ORDER BY score DESC
            LIMIT $2
        """

        # Let PostgreSQL parse natural-language text safely.
        tsquery = query_text

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, tsquery, top_k, *filter_args)

            results = []
            for rank, row in enumerate(rows, start=1):
                chunk = Chunk(
                    id=row["id"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                    vector=list(row["content_vector"]) if row["content_vector"] else None,
                    metadata=row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"]) if row["metadata"] else {},
                    score=float(row["score"]),
                )
                results.append(SearchResult(chunk=chunk, score=float(row["score"]), rank=rank))

            return results

    async def hybrid_search(
        self,
        query_text: str,
        query_vector: List[float],
        top_k: int = 5,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
        filter_dict: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Perform hybrid search using Reciprocal Rank Fusion (RRF).

        Combines vector search and keyword search results using RRF algorithm.

        Args:
            query_text: Query text for keyword search
            query_vector: Query embedding for vector search
            top_k: Number of results to return
            vector_weight: Weight for vector search (not used in RRF, but kept for interface compatibility)
            keyword_weight: Weight for keyword search (not used in RRF, but kept for interface compatibility)
            filter_dict: Optional metadata filters
            **kwargs: Additional parameters

        Returns:
            List of search results ranked by RRF score
        """
        # Convert query vector to pgvector format
        vector_str = "[" + ",".join(map(str, query_vector)) + "]"

        # Let PostgreSQL parse natural-language text safely.
        tsquery = query_text

        # Build filter clause
        filter_clause, filter_args = self._build_filter_clause(filter_dict, 4)

        # RRF constant (typical value is 60)
        rrf_k = 60

        # Use CTE (Common Table Expressions) for RRF
        query = f"""
            WITH vector_results AS (
                SELECT
                    id,
                    content,
                    content_hash,
                    content_vector,
                    metadata || jsonb_build_object('avg_feedback_score', avg_feedback_score,
                    'num_feedback', num_feedback, 'retrieval_count', retrieval_count) AS metadata,
                    1 - (content_vector <=> $1::vector) AS vector_score,
                    ROW_NUMBER() OVER (ORDER BY content_vector <=> $1::vector) AS vector_rank
                FROM embeddings
                {filter_clause}
                ORDER BY content_vector <=> $1::vector
                LIMIT 50
            ),
            keyword_results AS (
                SELECT
                    id,
                    content,
                    content_hash,
                    content_vector,
                    metadata || jsonb_build_object('avg_feedback_score', avg_feedback_score,
                    'num_feedback', num_feedback, 'retrieval_count', retrieval_count) AS metadata,
                    ts_rank(content_tsvector, query) AS keyword_score,
                    ROW_NUMBER() OVER (ORDER BY ts_rank(content_tsvector, query) DESC) AS keyword_rank
                FROM embeddings, plainto_tsquery('english', $2) AS query
                WHERE content_tsvector @@ query
                {filter_clause.replace('WHERE', 'AND')}
                LIMIT 50
            )
            SELECT
                COALESCE(v.id, k.id) AS id,
                COALESCE(v.content, k.content) AS content,
                COALESCE(v.content_hash, k.content_hash) AS content_hash,
                COALESCE(v.content_vector, k.content_vector) AS content_vector,
                COALESCE(v.metadata, k.metadata) AS metadata,
                COALESCE(v.vector_score, 0) AS vector_score,
                COALESCE(k.keyword_score, 0) AS keyword_score,
                -- RRF score: 1/(k + rank)
                (
                    COALESCE(1.0 / ($3 + v.vector_rank), 0) +
                    COALESCE(1.0 / ($3 + k.keyword_rank), 0)
                ) AS rrf_score
            FROM vector_results v
            FULL OUTER JOIN keyword_results k ON v.id = k.id
            ORDER BY rrf_score DESC
            LIMIT $4
        """

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, vector_str, tsquery, rrf_k, top_k, *filter_args)

            results = []
            for rank, row in enumerate(rows, start=1):
                chunk = Chunk(
                    id=row["id"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                    vector=list(row["content_vector"]) if row["content_vector"] else None,
                    metadata=row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"]) if row["metadata"] else {},
                    score=float(row["rrf_score"]),
                )
                results.append(SearchResult(chunk=chunk, score=float(row["rrf_score"]), rank=rank))

            return results

    async def get_document_status(self, collection, document_id):
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT revision,
                (SELECT count(*) FROM embeddings WHERE metadata->>'collection'=$1
                 AND metadata->>'document_id'=$2) AS chunks
                FROM documents WHERE collection=$1 AND document_id=$2""", collection, document_id)
            return dict(row) if row else None

    async def replace_document(self, collection, document_id, revision, chunks):
        """Publish all chunks in one transaction, serialized with deletes/upserts."""
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                                   json.dumps([collection, document_id]))
                current = await conn.fetchval(
                    "SELECT revision FROM documents WHERE collection=$1 AND document_id=$2",
                    collection, document_id)
                if current == revision:
                    return False
                await conn.execute("DELETE FROM embeddings WHERE metadata->>'collection'=$1 AND metadata->>'document_id'=$2",
                                   collection, document_id)
                await conn.executemany("""INSERT INTO embeddings
                    (id, content, content_hash, content_vector, metadata)
                    VALUES ($1,$2,$3,$4::vector,$5::jsonb)""", [
                    (c.id, c.content, c.content_hash, "[" + ",".join(map(str, c.vector)) + "]",
                     json.dumps(c.metadata)) for c in chunks])
                await conn.execute("""INSERT INTO documents(collection,document_id,revision)
                    VALUES ($1,$2,$3) ON CONFLICT(collection,document_id) DO UPDATE
                    SET revision=EXCLUDED.revision,indexed_at=NOW()""", collection, document_id, revision)
        return True

    async def delete_document(self, collection, document_id):
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                                   json.dumps([collection, document_id]))
                await conn.execute("DELETE FROM embeddings WHERE metadata->>'collection'=$1 AND metadata->>'document_id'=$2",
                                   collection, document_id)
                await conn.execute("DELETE FROM documents WHERE collection=$1 AND document_id=$2",
                                   collection, document_id)

    async def get_chunk(self, chunk_id: UUID) -> Optional[Chunk]:
        """Get chunk by ID.

        Args:
            chunk_id: Chunk UUID

        Returns:
            Chunk if found, None otherwise
        """
        query = """
            SELECT id, content, content_hash, content_vector, metadata
            FROM embeddings
            WHERE id = $1
        """

        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(query, chunk_id)

            if not row:
                return None

            return Chunk(
                id=row["id"],
                content=row["content"],
                content_hash=row["content_hash"],
                vector=list(row["content_vector"]) if row["content_vector"] else None,
                metadata=row["metadata"],
            )

    async def update_chunk_metadata(
        self, chunk_id: UUID, metadata: Dict[str, Any]
    ) -> None:
        """Update chunk metadata.

        Args:
            chunk_id: Chunk UUID
            metadata: Metadata to update (will be merged with existing)
        """
        query = """
            UPDATE embeddings
            SET metadata = metadata || $1::jsonb,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $2
        """

        async with self.db_pool.acquire() as conn:
            await conn.execute(query, json.dumps(metadata), chunk_id)

    async def delete_chunk(self, chunk_id: UUID) -> None:
        """Delete chunk by ID.

        Args:
            chunk_id: Chunk UUID
        """
        query = "DELETE FROM embeddings WHERE id = $1"

        async with self.db_pool.acquire() as conn:
            await conn.execute(query, chunk_id)

    async def delete_by_filter(self, filter_dict: Dict[str, Any]) -> int:
        """Delete chunks matching filter.

        Args:
            filter_dict: Metadata filter

        Returns:
            Number of chunks deleted
        """
        filter_clause, filter_args = self._build_filter_clause(filter_dict)
        query = f"DELETE FROM embeddings {filter_clause} RETURNING id"

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *filter_args)
            return len(rows)

    async def count(self, filter_dict: Optional[Dict[str, Any]] = None) -> int:
        """Count chunks in index.

        Args:
            filter_dict: Optional metadata filter

        Returns:
            Number of chunks
        """
        filter_clause, filter_args = self._build_filter_clause(filter_dict)
        query = f"SELECT COUNT(*) FROM embeddings {filter_clause}"

        async with self.db_pool.acquire() as conn:
            return await conn.fetchval(query, *filter_args)

    def _build_filter_clause(self, filter_dict: Optional[Dict[str, Any]], offset: int = 0):
        """Return SQL and parameters; never interpolate metadata keys or values."""
        conditions, args = [], []
        for key, value in (filter_dict or {}).items():
            args.append(key)
            key_param = f"${offset + len(args)}::text"
            value_param = f"${offset + len(args) + 1}"
            if isinstance(value, list):
                args.append([str(v) for v in value])
                conditions.append(f"metadata->>{key_param} = ANY({value_param}::text[])")
            elif isinstance(value, bool):
                args.append(value)
                conditions.append(f"(metadata->>{key_param})::boolean = {value_param}::boolean")
            elif isinstance(value, (int, float)):
                args.append(value)
                conditions.append(f"(metadata->>{key_param})::numeric = {value_param}::numeric")
            elif isinstance(value, str):
                args.append(value)
                conditions.append(f"metadata->>{key_param} = {value_param}::text")
            else:
                raise ValueError(f"Unsupported metadata filter value for {key}")
        return ("WHERE " + " AND ".join(conditions) if conditions else "", args)
