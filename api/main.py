"""FastAPI application main entry point."""

import asyncpg
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import ConfigLoader, Settings, get_settings, set_config_loader
from config.models import ConfigurationProfile
from core.metrics import MetricsTracker
from core.pipeline import IngestionPipeline, IngestionRequest, QueryPipeline, QueryRequest
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
        self.config_loader: Optional[ConfigLoader] = None
        self.provider_factory: Optional[ProviderFactory] = None
        self.metrics_tracker: Optional[MetricsTracker] = None


app_state = AppState()


# ============================================
# Lifecycle Management
# ============================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    print("Starting RAG Testing Pipeline API...")

    # Load settings
    app_state.settings = get_settings()
    print(f"Environment: {app_state.settings.environment}")

    # Create database pool
    print("Connecting to database...")
    app_state.db_pool = await asyncpg.create_pool(
        host=app_state.settings.postgres_host,
        port=app_state.settings.postgres_port,
        database=app_state.settings.postgres_db,
        user=app_state.settings.postgres_user,
        password=app_state.settings.postgres_password,
        min_size=5,
        max_size=app_state.settings.db_pool_size,
    )
    print("Database connected!")

    # Initialize config loader
    app_state.config_loader = ConfigLoader(
        app_state.settings, app_state.db_pool)
    set_config_loader(app_state.config_loader)

    # Initialize provider factory
    app_state.provider_factory = ProviderFactory(app_state.db_pool)

    # Initialize metrics tracker
    app_state.metrics_tracker = MetricsTracker(app_state.db_pool)

    print("API ready! 🚀")

    yield

    # Shutdown
    print("Shutting down...")
    if app_state.db_pool:
        await app_state.db_pool.close()
    print("Shutdown complete.")


# ============================================
# FastAPI App
# ============================================

app = FastAPI(
    title="RAG Testing Pipeline API",
    description="Testing framework for RAG system with multiple providers",
    version="1.0.0",
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
    profile: str = Field(
        "default", description="Configuration profile name")
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

    query: str = Field(..., description="Question to answer")
    top_k: Optional[int] = Field(
        None, description="Number of results to retrieve")
    filters: Optional[Dict[str, Any]] = Field(
        None, description="Metadata filters")
    profile: str = Field(
        "default", description="Configuration profile name")
    retrieve_only: bool = Field(
        False, description="Skip LLM generation, return only retrieved chunks")


class FeedbackRequest(BaseModel):
    """Feedback endpoint request."""

    query_id: UUID = Field(..., description="Query UUID")
    score: int = Field(..., ge=0, le=10, description="Feedback score (0-10)")
    comment: Optional[str] = Field(None, description="Optional comment")


# ============================================
# Helper Functions
# ============================================


async def get_profile(profile_name: str) -> ConfigurationProfile:
    """Get configuration profile.

    Args:
        profile_name: Profile name

    Returns:
        ConfigurationProfile

    Raises:
        HTTPException: If profile not found
    """
    try:
        logger.debug(f"Looking up profile: {profile_name}")
        profile = await app_state.config_loader.get_profile_by_name(profile_name)
        logger.debug(f"Profile found: {profile.profile_name}")
        return profile
    except ValueError as e:
        logger.error(f"Profile lookup failed: {e}")
        raise HTTPException(
            status_code=404, detail=f"Profile '{profile_name}' not found")


# ============================================
# Endpoints
# ============================================


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "RAG Testing Pipeline API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
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

    # Get configuration profile
    profile = await get_profile(request.profile)

    # Create providers
    vector_store = app_state.provider_factory.create_vector_store(
        profile.provider_config.vector_store
    )
    embedding_provider = app_state.provider_factory.create_embedding_provider(
        profile.provider_config.embedding
    )

    # Create ingestion pipeline
    pipeline = IngestionPipeline(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        profile=profile,
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
            "profile": request.profile,
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
    4. Generates answer using LLM
    5. Returns answer with sources and citations

    Tracks all metrics to database.
    """
    # Debug: Log query input
    logger.debug(f"Query request received: query={request.query}, top_k={request.top_k}, "
                 f"retrieve_only={request.retrieve_only}, filters={request.filters}, profile={request.profile}")

    # Get configuration profile
    profile = await get_profile(request.profile)

    # Create providers
    vector_store = app_state.provider_factory.create_vector_store(
        profile.provider_config.vector_store
    )
    embedding_provider = app_state.provider_factory.create_embedding_provider(
        profile.provider_config.embedding
    )

    # Process query
    query_request = QueryRequest(
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
    )

    # Retrieve-only mode: Skip LLM generation, return chunks for Copilot to synthesize
    if request.retrieve_only:
        logger.debug(
            "Processing query in retrieve_only mode (no LLM generation)")
        import time
        import uuid
        start_time = time.time()

        # Only do retrieval
        from core.pipeline.query_pipeline import Retriever
        retriever = Retriever(
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            config=profile.retrieval_config,
        )

        retrieved_results = await retriever.retrieve(
            query=request.query,
            top_k=request.top_k or profile.retrieval_config.top_k,
            filters=request.filters,
        )

        retrieval_time_ms = (time.time() - start_time) * 1000
        logger.debug(
            f"Retrieval completed: {len(retrieved_results)} results in {retrieval_time_ms:.2f}ms")

        # Generate query_id for feedback tracking
        query_id = uuid.uuid4()

        # Deduplicate chunks by source URL, keeping highest score for each unique URL
        chunks_by_url = {}
        for result in retrieved_results:
            metadata = result.metadata
            source_url = metadata.get("source_url") or metadata.get("url", "")

            # Normalize URL: strip whitespace and handle None/empty
            if source_url:
                source_url = str(source_url).strip()

            # Skip entries without a valid source URL
            if not source_url:
                continue

            # If we've seen this URL before, only keep if this has a higher score
            if source_url in chunks_by_url:
                if result.boosted_score > chunks_by_url[source_url]["score"]:
                    chunks_by_url[source_url] = {
                        "content": result.content,
                        "title": metadata.get("title", "Unknown"),
                        "url": source_url,
                        "score": result.boosted_score,
                        "section": metadata.get("chunk_index"),
                        "last_modified": metadata.get("last_modified"),
                    }
            else:
                # First time seeing this URL
                chunks_by_url[source_url] = {
                    "content": result.content,
                    "title": metadata.get("title", "Unknown"),
                    "url": source_url,
                    "score": result.boosted_score,
                    "section": metadata.get("chunk_index"),
                    "last_modified": metadata.get("last_modified"),
                }

        # Format chunks for Copilot with citations
        chunks = [
            {
                "citation": f"[{i+1}]",
                "content": chunk["content"],
                "title": chunk["title"],
                "url": chunk["url"],
                "score": chunk["score"],
                "section": chunk["section"],
                "last_modified": chunk["last_modified"],
            }
            for i, chunk in enumerate(chunks_by_url.values(), start=1)
        ]

        # Save query and metrics for feedback tracking
        try:
            import json
            async with app_state.db_pool.acquire() as conn:
                # Insert into queries table
                await conn.execute(
                    """
                    INSERT INTO queries (
                        query_id, query_text, profile_id, config_snapshot,
                        response, retrieved_chunks, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
                    """,
                    query_id,
                    request.query,
                    profile.profile_id,
                    json.dumps(profile.model_dump(mode="json")),
                    # Store chunks in response field
                    json.dumps({"chunks": chunks}),
                    [],  # Empty retrieved_chunks array
                )

                # Insert into metrics table
                await conn.execute(
                    """
                    INSERT INTO metrics (
                        metric_id, query_id,
                        latency_embedding_ms, latency_retrieval_ms,
                        latency_llm_ms, latency_total_ms,
                        cost_embedding_usd, cost_llm_usd, cost_total_usd,
                        num_chunks_retrieved, num_chunks_used, avg_chunk_score,
                        prompt_tokens, completion_tokens, total_tokens,
                        created_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8,
                        $9, $10, $11, $12, $13, $14, CURRENT_TIMESTAMP
                    )
                    """,
                    query_id,
                    0.0,  # No embedding latency (cached in retriever)
                    retrieval_time_ms,
                    0.0,  # No LLM latency
                    retrieval_time_ms,  # Total latency = retrieval only
                    0.0,  # No embedding cost
                    0.0,  # No LLM cost
                    0.0,  # No total cost
                    len(chunks),  # Number of chunks retrieved
                    0,  # No chunks used by LLM
                    sum(c["score"] for c in chunks) /
                    len(chunks) if chunks else 0.0,  # Average score
                    0,  # No prompt tokens
                    0,  # No completion tokens
                    0,  # No total tokens
                )
        except Exception as e:
            # Log error but don't fail the request
            logger.warning(f"Failed to save retrieve_only query: {e}")

        response_data = {
            "query_id": str(query_id),
            "chunks": chunks,
            "metrics": {
                "latency_ms": retrieval_time_ms,
                "chunks_retrieved": len(chunks),
                "profile": request.profile,
            }
        }

        # Debug: Log response output
        logger.debug(f"Retrieve-only response: query_id={query_id}, chunks_count={len(chunks)}, "
                     f"latency_ms={retrieval_time_ms:.2f}")
        logger.debug(
            f"Retrieve-only chunks URLs: {[chunk.get('url', 'N/A') for chunk in chunks[:5]]}")

        return response_data

    llm_provider = app_state.provider_factory.create_llm_provider(
        profile.provider_config.llm
    )

    # Create query pipeline
    pipeline = QueryPipeline(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        llm_provider=llm_provider,
        profile=profile,
    )

    # Standard mode: Full RAG pipeline with LLM generation
    logger.debug("Processing query in standard mode (with LLM generation)")
    response = await pipeline.process_query(query_request)
    logger.debug(f"Query pipeline completed: query_id={response.query_id}, "
                 f"sources_count={len(response.sources)}, latency_total_ms={response.metrics.latency_total_ms:.2f}")

    # Save metrics to database
    await app_state.metrics_tracker.save_query(
        query_response=response,
        query_text=request.query,
        profile_id=profile.profile_id,
        config_snapshot=profile.model_dump(mode="json"),
        retrieved_chunk_ids=[],  # Will be populated after we extract chunk IDs properly
    )

    # Format response
    response_data = {
        "query_id": str(response.query_id),
        "answer": response.answer,
        "sources": [
            {
                "citation": s.citation,
                "title": s.title,
                "url": s.url,
                "score": s.score,
                "section": s.section,
                "last_modified": s.last_modified.isoformat() if hasattr(s.last_modified, 'isoformat') else s.last_modified,
            }
            for s in response.sources
        ],
        "metrics": {
            "latency_ms": response.metrics.latency_total_ms,
            "latency_retrieval_ms": response.metrics.latency_retrieval_ms,
            "latency_llm_ms": response.metrics.latency_llm_ms,
            "cost_usd": response.metrics.cost_total_usd,
            "chunks_retrieved": response.metrics.num_chunks_retrieved,
            "tokens_used": response.metrics.total_tokens,
        },
        "profile": {
            "name": response.profile_name,
            "version": response.profile_version,
        },
    }

    # Debug: Log response output
    logger.debug(f"Standard response: query_id={response.query_id}, answer_length={len(response.answer)}, "
                 f"sources_count={len(response.sources)}, latency_total_ms={response.metrics.latency_total_ms:.2f}")
    logger.debug(
        f"Standard response sources URLs: {[s.url for s in response.sources[:5]]}")

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
async def get_metrics(profile: Optional[str] = None):
    """Get aggregated metrics.

    Returns statistics like:
    - Total queries
    - Average latency
    - Average cost
    - Satisfaction rate
    """
    profile_id = None
    if profile and profile != "default":
        prof = await get_profile(profile)
        profile_id = prof.profile_id

    summary = await app_state.metrics_tracker.get_metrics_summary(profile_id)

    return {
        "profile": profile or "all",
        "metrics": summary,
    }


@app.get("/profiles")
async def list_profiles():
    """List available configuration profiles."""
    async with app_state.db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT profile_name, version, description, created_at, is_active
            FROM configuration_profiles
            ORDER BY created_at DESC
            """
        )

    profiles = [
        {
            "name": row["profile_name"],
            "version": row["version"],
            "description": row["description"],
            "created_at": row["created_at"].isoformat(),
            "is_active": row["is_active"],
        }
        for row in rows
    ]

    return {"profiles": profiles}


@app.get("/report")
async def comparison_report():
    """Get comparison report across all profiles.

    Compares:
    - Latency (retrieval, LLM, total)
    - Cost per query
    - User satisfaction (feedback scores)
    - Chunk sizes and embedding dimensions
    """
    async with app_state.db_pool.acquire() as conn:
        # Get profile performance metrics
        rows = await conn.fetch(
            """
            SELECT
                cp.profile_name,
                cp.version,
                -- Configuration
                (cp.chunking_config->>'chunk_size')::int as chunk_size,
                (cp.provider_config->'embedding'->>'dimension')::int as embedding_dim,
                (cp.provider_config->'embedding'->>'model') as embedding_model,
                (cp.provider_config->'llm'->>'model') as llm_model,
                (cp.retrieval_config->>'top_k')::int as top_k,
                (cp.retrieval_config->>'hybrid_search')::boolean as hybrid_search,
                -- Query metrics
                COUNT(DISTINCT q.query_id) as total_queries,
                ROUND(AVG(m.latency_retrieval_ms)::numeric, 2) as avg_retrieval_ms,
                ROUND(AVG(m.latency_llm_ms)::numeric, 2) as avg_llm_ms,
                ROUND(AVG(m.latency_total_ms)::numeric, 2) as avg_total_ms,
                ROUND(AVG(m.cost_total_usd)::numeric, 6) as avg_cost_usd,
                ROUND(AVG(m.num_chunks_retrieved)::numeric, 1) as avg_chunks,
                ROUND(AVG(m.avg_chunk_score)::numeric, 3) as avg_relevance,
                -- Feedback metrics
                COUNT(f.feedback_id) as feedback_count,
                ROUND(AVG(f.score)::numeric, 2) as avg_satisfaction,
                COUNT(f.feedback_id) FILTER (WHERE f.score >= 7) as satisfied_count,
                ROUND(
                    CASE
                        WHEN COUNT(f.feedback_id) > 0
                        THEN COUNT(f.feedback_id) FILTER (WHERE f.score >= 7)::numeric / COUNT(f.feedback_id)::numeric
                        ELSE NULL
                    END,
                    3
                ) as satisfaction_rate
            FROM configuration_profiles cp
            LEFT JOIN queries q ON cp.profile_id = q.profile_id
            LEFT JOIN metrics m ON q.query_id = m.query_id
            LEFT JOIN feedback f ON q.query_id = f.query_id
            WHERE cp.is_active = true OR cp.profile_id IS NULL
            GROUP BY
                cp.profile_id,
                cp.profile_name,
                cp.version,
                cp.chunking_config,
                cp.provider_config,
                cp.retrieval_config
            ORDER BY total_queries DESC, avg_satisfaction DESC
            """
        )

        profiles = []
        for row in rows:
            profiles.append({
                "profile": row["profile_name"],
                "version": row["version"],
                "config": {
                    "chunk_size": row["chunk_size"],
                    "embedding_dim": row["embedding_dim"],
                    "embedding_model": row["embedding_model"],
                    "llm_model": row["llm_model"],
                    "top_k": row["top_k"],
                    "hybrid_search": row["hybrid_search"],
                },
                "performance": {
                    "total_queries": row["total_queries"],
                    "avg_retrieval_ms": float(row["avg_retrieval_ms"]) if row["avg_retrieval_ms"] else None,
                    "avg_llm_ms": float(row["avg_llm_ms"]) if row["avg_llm_ms"] else None,
                    "avg_total_ms": float(row["avg_total_ms"]) if row["avg_total_ms"] else None,
                    "avg_cost_usd": float(row["avg_cost_usd"]) if row["avg_cost_usd"] else 0.0,
                    "avg_chunks_retrieved": float(row["avg_chunks"]) if row["avg_chunks"] else None,
                    "avg_relevance_score": float(row["avg_relevance"]) if row["avg_relevance"] else None,
                },
                "feedback": {
                    "total_feedback": row["feedback_count"],
                    "avg_satisfaction": float(row["avg_satisfaction"]) if row["avg_satisfaction"] else None,
                    "satisfied_count": row["satisfied_count"],
                    "satisfaction_rate": float(row["satisfaction_rate"]) if row["satisfaction_rate"] else None,
                },
            })

    return {
        "report_generated_at": None,  # Will be set by response
        "total_profiles": len(profiles),
        "profiles": profiles,
    }


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
