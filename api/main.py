"""FastAPI application main entry point."""

import asyncpg
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from config import Settings, get_settings
from core.metrics import MetricsTracker
from core.pipeline import IngestionPipeline, IngestionRequest
from core.retrieval import Retriever
from core.providers import ProviderFactory

# Setup logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============================================
# Global State
# ============================================


class AppState:
    """Application state container."""

    def __init__(self):
        self.db_pool: Optional[asyncpg.Pool] = None
        self.settings: Optional[Settings] = None
        self.provider_factory: Optional[ProviderFactory] = None
        self.metrics_tracker: Optional[MetricsTracker] = None


app_state = AppState()


# ============================================
# Lifecycle Management
# ============================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    try:
        app_state.settings = get_settings()
        app_state.db_pool = await asyncpg.create_pool(
            host=app_state.settings.postgres_host,
            port=app_state.settings.postgres_port,
            database=app_state.settings.postgres_db,
            user=app_state.settings.postgres_user,
            password=app_state.settings.postgres_password,
            min_size=1,
            max_size=app_state.settings.db_pool_size,
            command_timeout=30,
        )
        app_state.provider_factory = ProviderFactory(app_state.db_pool)
        app_state.metrics_tracker = MetricsTracker(app_state.db_pool)
        await check_readiness(check_model=True)
        logger.info("Retrieval API ready")
        yield
    finally:
        try:
            if app_state.provider_factory:
                await app_state.provider_factory.close()
        finally:
            if app_state.db_pool:
                await app_state.db_pool.close()
            app_state.db_pool = None
            app_state.provider_factory = None
            app_state.metrics_tracker = None


async def check_readiness(check_model: bool = False):
    """Check database/schema and environment configuration without changing data."""
    if app_state.db_pool is None:
        raise RuntimeError("Database pool is unavailable")
    async with app_state.db_pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
        for table in ("embeddings", "queries", "metrics", "feedback"):
            if not await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"):
                raise RuntimeError(f"Missing table {table}; initialize a fresh database with db/init.sql")
        try:
            await conn.execute("SELECT latency_rerank_ms FROM metrics LIMIT 0")
        except asyncpg.UndefinedColumnError as exc:
            raise RuntimeError("Outdated schema: reset service tables with db/cleanup.sql and db/init.sql, then re-ingest documents") from exc
        if await conn.fetchval("""
            SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'embeddings' AND column_name = 'profile_id')
        """):
            raise RuntimeError("Outdated schema: reset with db/cleanup.sql and db/init.sql, then re-ingest documents")
        vector_type = await conn.fetchval("""
            SELECT format_type(atttypid, atttypmod) FROM pg_attribute
            WHERE attrelid = 'embeddings'::regclass AND attname = 'content_vector'
        """)
    settings = app_state.settings
    dimension = settings.embedding_config.dimension
    if vector_type != f"vector({dimension})" or settings.vector_store_config.dimension != dimension:
        raise RuntimeError("Embedding configuration dimension does not match embeddings.content_vector; use the matching model/dimension or explicitly migrate and re-embed")
    if check_model:
        await app_state.provider_factory.get_embedding_provider(settings.embedding_config)
        await app_state.provider_factory.get_reranker(settings.retrieval_config, settings.embedding_config.device)


# ============================================
# FastAPI App
# ============================================

app = FastAPI(
    title="RAG Retrieval API",
    description="Standalone ingestion, retrieval and optional local reranking",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# Request/Response Models
# ============================================


class IngestRequest(BaseModel):
    """Ingest endpoint request."""

    url: Optional[str] = Field(None, description="URL to scrape")
    content: Optional[str] = Field(None, description="Direct content")
    title: Optional[str] = Field(None, description="Document title")
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Additional metadata")
    # Crawling parameters
    depth: int = Field(
        1, ge=1, le=5, description="Maximum crawl depth (1 = only URL, 2+ = recursive)")
    max_pages: int = Field(
        50, ge=1, le=500, description="Maximum pages to scrape")
    url_patterns: Optional[List[str]] = Field(
        None, description="URL patterns to include (glob syntax)")
    exclude_patterns: Optional[List[str]] = Field(
        None, description="URL patterns to exclude (glob syntax)")


class QueryRequestAPI(BaseModel):
    """Query endpoint request."""

    query: str = Field(..., min_length=1, description="Question to search")
    top_k: Optional[int] = Field(
        None, gt=0, description="Number of results to retrieve")
    filters: Optional[Dict[str, Any]] = Field(
        None, description="Metadata filters")
    retrieve_only: Literal[True] = Field(
        True, description="Compatibility field: only retrieval is supported; false is rejected")


    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class FeedbackRequest(BaseModel):
    """Feedback endpoint request."""

    query_id: UUID = Field(..., description="Query UUID")
    score: int = Field(..., ge=0, le=10, description="Feedback score (0-10)")
    comment: Optional[str] = Field(None, description="Optional comment")


# ============================================
# Endpoints
# ============================================


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "RAG Retrieval API",
        "version": "2.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        await check_readiness()
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "unavailable or incompatible"})
    return {"status": "healthy", "database": "connected"}


@app.post("/ingest")
async def ingest(request: IngestRequest):
    """Ingest document (URL or direct content).

    This endpoint:
    1. Scrapes content from URL (if provided) or uses direct content
    2. Chunks the document
    3. Generates embeddings
    4. Stores in vector database

    Returns job statistics.
    """
    if not request.url and not request.content:
        raise HTTPException(
            status_code=400, detail="Must provide either url or content"
        )

    settings = app_state.settings

    # Create providers
    vector_store = app_state.provider_factory.create_vector_store(
        settings.vector_store_config
    )
    embedding_provider = await app_state.provider_factory.get_embedding_provider(
        settings.embedding_config
    )

    # Create ingestion pipeline
    pipeline = IngestionPipeline(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        chunking_config=settings.chunking_config,
    )

    # Process ingestion
    try:
        ingestion_request = IngestionRequest(
            url=request.url,
            content=request.content,
            title=request.title,
            metadata=request.metadata,
            depth=request.depth,
            max_pages=request.max_pages,
            url_patterns=request.url_patterns,
            exclude_patterns=request.exclude_patterns,
        )

        result = await pipeline.ingest(ingestion_request)

        if not result.success:
            raise HTTPException(status_code=500, detail=result.error_message)

        return {
            "job_id": str(result.job_id),
            "success": result.success,
            "pages_scraped": result.pages_scraped,
            "chunks_created": result.chunks_created,
            "embeddings_generated": result.embeddings_generated,
            "processing_time_ms": result.processing_time_ms,
        }

    finally:
        await pipeline.close()


@app.post("/query")
async def query(request: QueryRequestAPI):
    """Query the knowledge base.

    This endpoint:
    1. Generates embedding for the query
    2. Performs hybrid search (vector + keyword)
    3. Applies metadata boosting (recency, quality, popularity)
    4. Optionally reranks and returns chunks with citations

    Tracks all metrics to database.
    """
    # Debug: Log query input
    logger.debug(f"Query request received: query={request.query}, top_k={request.top_k}, "
                 f"retrieve_only={request.retrieve_only}, filters={request.filters}")

    settings = app_state.settings

    # Create providers
    vector_store = app_state.provider_factory.create_vector_store(
        settings.vector_store_config
    )
    embedding_provider = await app_state.provider_factory.get_embedding_provider(
        settings.embedding_config
    )

    import time
    import uuid
    start_time = time.time()

    retriever = Retriever(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        config=settings.retrieval_config,
        reranker=await app_state.provider_factory.get_reranker(
            settings.retrieval_config, settings.embedding_config.device
        ),
    )

    retrieved_results = await retriever.retrieve(
        query=request.query,
        top_k=request.top_k or settings.retrieval_config.top_k,
        filters=request.filters,
    )

    retrieval_time_ms = (time.time() - start_time) * 1000
    logger.debug(
        f"Retrieval completed: {len(retrieved_results)} results in {retrieval_time_ms:.2f}ms")

    # Generate query_id for feedback tracking
    query_id = uuid.uuid4()

    # Preserve all ranked chunks, including direct content without a URL.
    chunks = [
        {
            "chunk_id": result.chunk_id,
            "citation": f"[{i}]",
            "content": result.content,
            "title": result.metadata.get("title") or "Unknown",
            "url": str(result.metadata.get("source_url") or result.metadata.get("url") or "").strip(),
            "score": result.boosted_score,
            "rerank_score": result.rerank_score,
            "section": result.metadata.get("chunk_index"),
            "last_modified": result.metadata.get("last_modified"),
        }
        for i, result in enumerate(retrieved_results, start=1)
    ]
    feedback_available = True
    try:
        await app_state.metrics_tracker.save_retrieval(
            query_id, request.query, chunks, retrieval_time_ms, retriever.rerank_time_ms
        )
    except Exception:
        feedback_available = False
        logger.exception("Failed to save retrieval query %s", query_id)

    response_data = {
        "query_id": str(query_id),
        "feedback_available": feedback_available,
        "chunks": chunks,
        "metrics": {
            "latency_ms": retrieval_time_ms,
            "latency_rerank_ms": retriever.rerank_time_ms,
            "chunks_retrieved": len(chunks),
        }
    }

    # Debug: Log response output
    logger.debug(f"Retrieve-only response: query_id={query_id}, chunks_count={len(chunks)}, "
                 f"latency_ms={retrieval_time_ms:.2f}")
    logger.debug(
        f"Retrieve-only chunks URLs: {[chunk.get('url', 'N/A') for chunk in chunks[:5]]}")

    return response_data


@app.post("/feedback")
async def feedback(request: FeedbackRequest):
    """Submit user feedback for a query.

    Feedback score: 0-10
    - 0-2: Completely wrong
    - 3-4: Partially correct
    - 5-6: Acceptable
    - 7-8: Good
    - 9-10: Excellent

    Score >= 7 is considered "satisfied"
    """
    await app_state.metrics_tracker.save_feedback(
        query_id=request.query_id,
        score=request.score,
        comment=request.comment,
    )

    return {
        "success": True,
        "message": "Feedback saved",
        "query_id": str(request.query_id),
        "score": request.score,
    }


@app.get("/metrics")
async def get_metrics():
    """Return aggregate retrieval latency and feedback statistics."""
    return {"metrics": await app_state.metrics_tracker.get_metrics_summary()}


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    """Global exception handler."""
    print(f"Error processing request: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=settings.debug,
    )
