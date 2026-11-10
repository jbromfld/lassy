"""Transport-independent regression checks, without databases or model downloads."""

import asyncio
import os
import subprocess
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from lassy.config import ChunkingConfig, Settings
from lassy.documents import DocumentRequest, DocumentSection, upsert_document
from lassy.retrieval import Retriever
from lassy.types import Chunk, EmbeddingProvider, SearchResult


def settings(**overrides):
    return Settings(
        _env_file=None, postgres_user="test", postgres_password="test", **overrides
    )


def embedder(dimension=768):
    model = Mock(
        get_model_name=lambda: "test",
        get_dimension=lambda: dimension,
        chunking_identity=lambda: {},
        split_text=lambda text: [text],
    )
    model.prepare_document = EmbeddingProvider.prepare_document.__get__(model)
    model.embed_batch = AsyncMock(
        side_effect=lambda texts: [[1.0] + [0.0] * (dimension - 1) for _ in texts]
    )
    model.embed_text = AsyncMock(return_value=[1.0] + [0.0] * (dimension - 1))
    model.close = AsyncMock()
    return model


class MemoryStore:
    """Domain fake for pipeline tests; process coordination is tested against PostgreSQL."""

    def __init__(self):
        self.status = None
        self.chunks = []
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def document_operation(self, *_):
        async with self.lock:
            yield self

    async def get_document_status(self, *_):
        return self.status

    async def replace_document(
        self, _collection, _id, revision, chunks, *, embedding_revision
    ):
        self.chunks = chunks
        self.status = dict(
            revision=revision, embedding_revision=embedding_revision, chunks=len(chunks)
        )
        return True

    async def update_document_metadata(
        self, _collection, _id, revision, _embedding_revision, metadata
    ):
        self.status["revision"] = revision
        for chunk in self.chunks:
            chunk.metadata = {
                **metadata,
                **{
                    k: chunk.metadata[k]
                    for k in ("section_ref", "chunk_index", "total_chunks")
                },
            }
        return True


class DocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_sections_identity_retries_and_metadata_reuse(self):
        store = MemoryStore()
        embed = embedder()
        doc = DocumentRequest(
            content="Unused",
            sections=[
                DocumentSection(ref="p1", text="Short evidence."),
                DocumentSection(ref="p2", text="Short evidence."),
            ],
        )
        first = await upsert_document(
            store, embed, ChunkingConfig(), "corpus", "1", doc
        )
        self.assertEqual(
            [c.metadata["section_ref"] for c in store.chunks], ["p1", "p2"]
        )
        ids = [c.id for c in store.chunks]
        self.assertEqual(len(set(ids)), 2)
        retry = await upsert_document(
            store, embed, ChunkingConfig(), "corpus", "1", doc
        )
        self.assertFalse(retry["changed"])
        changed = await upsert_document(
            store,
            embed,
            ChunkingConfig(),
            "corpus",
            "1",
            doc.model_copy(update={"title": "New title"}),
        )
        self.assertNotEqual(first["revision"], changed["revision"])
        self.assertEqual(changed["embeddings_generated"], 0)
        self.assertEqual(ids, [c.id for c in store.chunks])
        self.assertEqual(embed.embed_batch.await_count, 1)

    async def test_failed_embeddings_never_publish(self):
        store = MemoryStore()
        embed = embedder()
        embed.embed_batch.side_effect = RuntimeError("private failure")
        with self.assertRaises(RuntimeError):
            await upsert_document(
                store,
                embed,
                ChunkingConfig(),
                "c",
                "1",
                DocumentRequest(content="Evidence"),
            )
        self.assertIsNone(store.status)

    async def test_concurrent_retries_embed_once_and_token_splits_keep_refs(self):
        store = MemoryStore()
        embed = embedder()
        embed.split_text = lambda text: [text[:4], text[4:]]
        doc = DocumentRequest(
            content="Unused",
            sections=[DocumentSection(ref="p9", text="Eight characters")],
        )
        results = await asyncio.gather(
            *(
                upsert_document(store, embed, ChunkingConfig(), "c", "1", doc)
                for _ in range(3)
            )
        )
        self.assertEqual(sum(r["changed"] for r in results), 1)
        self.assertEqual("".join(c.content for c in store.chunks), "Eight characters")
        self.assertTrue(all(c.metadata["section_ref"] == "p9" for c in store.chunks))


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_reranking_overrides_candidate_order_without_losing_provenance(self):
        config = settings(enable_reranking=True).retrieval_config
        rows = [
            SearchResult(
                Chunk(uuid4(), text, text, metadata={"document_id": text}), score, i
            )
            for i, (text, score) in enumerate([("a", 0.5), ("b", 0.4)], 1)
        ]
        store = Mock(hybrid_search=AsyncMock(return_value=rows))
        reranker = Mock(score=AsyncMock(return_value=[-2.0, 5.0]))
        r = Retriever(store, embedder(), config, reranker)
        result = await r.retrieve("query", top_k=2)
        self.assertEqual([v.metadata["document_id"] for v in result], ["b", "a"])
        self.assertEqual([v.rank for v in result], [1, 2])
        self.assertEqual([v.rerank_score for v in result], [5.0, -2.0])
        for scores in ([float("nan"), 0.0], [1.0]):
            reranker.score.return_value = scores
            with self.assertRaises((ValueError, RuntimeError)):
                await r.retrieve("query")

    async def test_disabled_reranking_never_calls_model_and_ignores_old_feedback(self):
        rows = [
            SearchResult(
                Chunk(
                    uuid4(),
                    "a",
                    "a",
                    metadata={
                        "avg_feedback_score": 10,
                        "num_feedback": 100,
                        "retrieval_count": 1000,
                    },
                ),
                0.4,
                1,
            )
        ]
        reranker = Mock(score=AsyncMock())
        result = await Retriever(
            Mock(hybrid_search=AsyncMock(return_value=rows)),
            embedder(),
            settings().retrieval_config,
            reranker,
        ).retrieve("q")
        reranker.score.assert_not_called()
        self.assertEqual(result[0].boosted_score, 0.4)


class ConfigTests(unittest.TestCase):
    def test_no_implicit_dotenv_or_model_download(self):
        import tempfile

        from lassy.config import load_settings

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text(
                "POSTGRES_USER=ambient\nPOSTGRES_PASSWORD=secret\nHF_HOME=cache\n"
            )
            old = os.getcwd()
            try:
                os.chdir(directory)
                with (
                    patch.dict(os.environ, {}, clear=True),
                    self.assertRaises(ValidationError),
                ):
                    load_settings()
                with patch.dict(
                    os.environ,
                    {"LASSY_ENV_FILE": str(Path(directory, ".env"))},
                    clear=True,
                ):
                    self.assertEqual(load_settings().postgres_user, "ambient")
                    self.assertEqual(
                        os.environ["HF_HOME"], str(Path(directory, "cache").resolve())
                    )
            finally:
                os.chdir(old)
        from lassy.models import offline_snapshot

        with patch(
            "huggingface_hub.snapshot_download",
            side_effect=RuntimeError("private token"),
        ) as download:
            with self.assertRaisesRegex(RuntimeError, "lassy-models prepare"):
                offline_snapshot("model", "f" * 40)
            self.assertTrue(download.call_args.kwargs["local_files_only"])

    def test_pinned_settings_and_limits(self):
        for overrides in (
            {"chunk_overlap": 300},
            {"chunk_size": 0},
            {"min_chunk_size": 400},
            {"local_embedding_batch_size": 0},
            {"inference_queue_timeout_seconds": 0},
            {"retrieval_top_k": 101},
            {"local_embedding_revision": "main"},
            {"pgvector_dimension": 384},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                settings(**overrides)
        from lassy.types import QueryRequest

        for body in (
            {"query": " "},
            {"query": "x" * 4001},
            {"query": "q", "top_k": 101},
            {"query": "q", "retrieve_only": False},
            {"query": "q", "filters": {"x": {}}},
        ):
            with self.assertRaises(ValidationError):
                QueryRequest.model_validate(body)

    def test_fresh_schema_has_no_tracking_tables(self):
        schema = Path("lassy/schema.sql").read_text()
        self.assertEqual(schema, Path("db/init.sql").read_text())
        for name in ("queries", "metrics", "feedback"):
            self.assertNotIn("CREATE TABLE " + name, schema)

    def test_startup_failure_has_no_protocol_noise_or_secret(self):
        result = subprocess.run(
            [os.sys.executable, "-m", "lassy.mcp"],
            env={
                **os.environ,
                "LASSY_ENV_FILE": "/nonexistent/lassy-config",
                "POSTGRES_PORT": "invalid-private-value",
            },
            input="",
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("invalid-private-value", result.stderr)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_startup_and_shutdown_close_all_resources(self):
        from lassy.service import open_service

        pool = Mock(close=AsyncMock())
        model = Mock(close=AsyncMock())
        reranker = Mock(close=AsyncMock())
        with (
            patch("lassy.service.create_pool", AsyncMock(return_value=pool)),
            patch(
                "lassy.service.load_models", AsyncMock(return_value=(model, reranker))
            ),
            patch(
                "lassy.service.Service.ready",
                AsyncMock(side_effect=RuntimeError("unverified index")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "unverified"):
                async with open_service(settings()):
                    self.fail("startup must fail")
        pool.close.assert_awaited_once()
        model.close.assert_awaited_once()
        reranker.close.assert_awaited_once()
        pool.close.reset_mock()
        model.close.reset_mock()
        reranker.close.reset_mock()
        reranker.close.side_effect = RuntimeError("shutdown failed")
        with (
            patch("lassy.service.create_pool", AsyncMock(return_value=pool)),
            patch(
                "lassy.service.load_models", AsyncMock(return_value=(model, reranker))
            ),
            patch("lassy.service.Service.ready", AsyncMock()),
        ):
            with self.assertRaisesRegex(RuntimeError, "shutdown"):
                async with open_service(settings()):
                    pass
        model.close.assert_awaited_once()
        pool.close.assert_awaited_once()
