"""Atomic retrieval tracking and feedback statistics."""
import json
from typing import Optional
from uuid import UUID

from asyncpg import Pool


class MetricsTracker:
    def __init__(self, db_pool: Pool):
        self.db_pool = db_pool

    async def save_retrieval(self, query_id, query_text, chunks, latency_ms, rerank_ms=0.0):
        ids = [UUID(c['chunk_id']) for c in chunks]
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO queries (query_id, query_text, response, retrieved_chunks)
                       VALUES ($1, $2, $3::jsonb, $4::uuid[])""",
                    query_id, query_text,
                    json.dumps({'chunks': [{key: c[key] for key in (
                        'chunk_id', 'citation', 'score', 'collection', 'document_id', 'revision') if key in c}
                        for c in chunks]}, default=str), ids,
                )
                await conn.execute(
                    """INSERT INTO metrics (query_id, latency_retrieval_ms, latency_rerank_ms,
                       latency_total_ms, num_chunks_retrieved, avg_chunk_score)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    query_id, max(0.0, latency_ms-rerank_ms), rerank_ms, latency_ms, len(chunks),
                    sum(c['score'] for c in chunks) / len(chunks) if chunks else 0.0,
                )
                if ids:
                    await conn.execute('UPDATE embeddings SET retrieval_count = retrieval_count + 1 WHERE id = ANY($1::uuid[])', ids)

    async def save_feedback(self, query_id: UUID, score: int, comment: Optional[str] = None):
        if not 0 <= score <= 10:
            raise ValueError('Score must be between 0 and 10')
        async with self.db_pool.acquire() as conn:
            await conn.execute('INSERT INTO feedback (query_id, score, comment) VALUES ($1, $2, $3)', query_id, score, comment)

    async def get_metrics_summary(self):
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                WITH performance AS (
                    SELECT COUNT(*) AS total_queries,
                        AVG(latency_total_ms) AS avg_latency_ms,
                        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_total_ms) AS median_latency_ms,
                        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_total_ms) AS p95_latency_ms,
                        AVG(latency_rerank_ms) AS avg_rerank_ms
                    FROM metrics
                ), satisfaction AS (
                    SELECT COUNT(*) AS total_feedback, AVG(f.score) AS avg_feedback_score,
                        AVG((f.score >= 7)::int) AS satisfaction_rate
                    FROM feedback f
                )
                SELECT * FROM performance CROSS JOIN satisfaction
            """)
        return {key: (int(value) if key in ('total_queries', 'total_feedback') else float(value) if value is not None else None)
                for key, value in dict(row).items()}

    async def prune_history(self, days=90, limit=1000, dry_run=False):
        """Remove one bounded batch of old unrated queries; preserve rated history."""
        if not 1 <= days <= 36500 or not 1 <= limit <= 10000:
            raise ValueError('days must be 1–36500 and limit 1–10000')
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch("""SELECT query_id FROM queries q
                    WHERE created_at < NOW() - make_interval(days => $1)
                    AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.query_id=q.query_id)
                    ORDER BY created_at, query_id LIMIT $2 FOR UPDATE OF q""", days, limit)
                ids = [row['query_id'] for row in rows]
                if not dry_run and ids:
                    # Recheck after acquiring row locks: concurrently submitted feedback wins.
                    ids = await conn.fetchval("""SELECT COALESCE(array_agg(query_id), '{}'::uuid[]) FROM queries q
                        WHERE query_id=ANY($1::uuid[]) AND NOT EXISTS
                        (SELECT 1 FROM feedback f WHERE f.query_id=q.query_id)""", ids)
                    await conn.execute('DELETE FROM metrics WHERE query_id=ANY($1::uuid[])', ids)
                    await conn.execute('DELETE FROM queries WHERE query_id=ANY($1::uuid[])', ids)
        return {'eligible': len(ids), 'deleted': 0 if dry_run else len(ids), 'dry_run': dry_run}
