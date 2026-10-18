"""Document ingestion pipeline orchestration."""

import time
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from config.models import ChunkingConfig
from core.ingestion.chunker import DocumentChunker
from core.pipeline.documents import DocumentRequest, upsert_document
from core.ingestion.scraper import DocumentScraper
from core.providers.base import EmbeddingProvider, VectorStore


@dataclass
class IngestionRequest:
    """Ingestion request."""

    url: Optional[str] = None  # URL to scrape
    content: Optional[str] = None  # Or direct content
    title: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    # Crawling parameters
    depth: int = 1  # Maximum crawl depth (1 = only URL, 2+ = recursive)
    max_pages: int = 50  # Maximum pages to scrape
    url_patterns: Optional[List[str]] = None  # URL patterns to include
    exclude_patterns: Optional[List[str]] = None  # URL patterns to exclude


@dataclass
class IngestionResult:
    """Ingestion result with statistics."""

    job_id: UUID
    success: bool
    pages_scraped: int
    chunks_created: int
    embeddings_generated: int
    processing_time_ms: float
    error_message: Optional[str] = None


class IngestionPipeline:
    """End-to-end ingestion pipeline.

    Orchestrates: Scrape → Chunk → Embed → Store
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        chunking_config: ChunkingConfig,
    ):
        """Initialize ingestion pipeline.

        Args:
            vector_store: Vector store instance
            embedding_provider: Embedding provider instance
            chunking_config: Chunking settings
        """
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider

        # Create chunker
        self.chunker = DocumentChunker(chunking_config)

        # Create scraper
        self.scraper = DocumentScraper()

    async def ingest(self, request: IngestionRequest) -> IngestionResult:
        """Ingest document(s) end-to-end.

        Args:
            request: Ingestion request

        Returns:
            Ingestion result with statistics
        """
        job_id = uuid4()
        start_time = time.time()

        try:
            # Step 1: Get content (scrape or use provided)
            documents = []
            if request.url:
                # Check if recursive crawling is requested
                if request.depth > 1:
                    # Recursive crawling
                    scraped_docs = await self.scraper.scrape_recursive(
                        start_url=request.url,
                        max_depth=request.depth,
                        max_pages=request.max_pages,
                        url_patterns=request.url_patterns,
                        exclude_patterns=request.exclude_patterns,
                    )
                    documents = scraped_docs
                else:
                    # Single page scraping
                    scraped_doc = await self.scraper.scrape_url(request.url)
                    documents = [scraped_doc]
            elif request.content:
                # Direct content provided
                from core.ingestion.scraper import ScrapedDocument
                metadata = request.metadata or {}
                if request.title:
                    metadata["title"] = request.title
                doc = ScrapedDocument(
                    url="direct-content",
                    title=request.title or "Direct Content",
                    content=request.content,
                    metadata=metadata,
                    scraped_at=datetime.utcnow(),
                )
                documents = [doc]
            else:
                raise ValueError("Must provide either url or content")

            if not documents:
                return IngestionResult(
                    job_id=job_id,
                    success=False,
                    pages_scraped=0,
                    chunks_created=0,
                    embeddings_generated=0,
                    processing_time_ms=(time.time() - start_time) * 1000,
                    error_message="No documents scraped",
                )

            # All ingestion entry points use the document pipeline and atomic publication.
            chunks_created = embeddings_generated = 0
            for doc in documents:
                identity = doc.url if doc.url != "direct-content" else doc.content
                document_id = hashlib.sha256(identity.encode()).hexdigest()
                document = DocumentRequest(content=doc.content,
                    title=doc.title, url=doc.url if doc.url != "direct-content" else None,
                    metadata=doc.metadata or {})
                result = await upsert_document(self.vector_store, self.embedding_provider,
                    self.chunker.config, "web" if request.url else "direct", document_id, document)
                chunks_created += result["chunks_created"]
                embeddings_generated += result["embeddings_generated"]

            return IngestionResult(
                job_id=job_id,
                success=True,
                pages_scraped=len(documents),
                chunks_created=chunks_created,
                embeddings_generated=embeddings_generated,
                processing_time_ms=(time.time() - start_time) * 1000,
            )

        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            return IngestionResult(
                job_id=job_id,
                success=False,
                pages_scraped=0,
                chunks_created=0,
                embeddings_generated=0,
                processing_time_ms=processing_time,
                error_message=str(e),
            )

    async def close(self):
        """Close resources."""
        await self.scraper.close()
