"""Regression checks for bounded ingestion, intact text, and model contention."""

import asyncio
import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError

from lassy.chunking import DocumentChunker
from lassy.concurrency import BusyError, InferenceGate
from lassy.config import ChunkingConfig, RetrievalConfig, Settings
from lassy.embedding import LocalEmbeddingProvider
from lassy.retrieval import Retriever
from lassy.types import EmbeddingProvider


def embedder():
    return Mock(
        get_model_name=lambda: "test",
        get_dimension=lambda: 2,
        chunking_identity=lambda: {},
        prepare_document=EmbeddingProvider.prepare_document.__get__(object()),
        split_text=lambda text: [text],
        embed_batch=AsyncMock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts]),
    )


class ChunkTests(unittest.TestCase):
    def test_every_word_survives_recursive_and_fixed_boundaries(self):
        words = [f"unique{i}" for i in range(610)]
        variants = [
            " ".join(words),
            " ".join(words[:300])
            + ". "
            + " ".join(words[300:600])
            + ". "
            + " ".join(words[600:]),
            "\n\n".join(" ".join(words[i : i + 60]) for i in range(0, len(words), 60)),
        ]
        for strategy in ("fixed", "recursive"):
            for text in variants:
                with self.subTest(strategy=strategy, ending=text[-20:]):
                    chunks = DocumentChunker(
                        ChunkingConfig(strategy=strategy)
                    ).chunk_document(text)
                    output = {
                        word.rstrip(".") for c in chunks for word in c.content.split()
                    }
                    self.assertTrue(set(words).issubset(output))
                    self.assertLessEqual(max(c.word_count for c in chunks), 500)
                    self.assertIn("unique609", chunks[-1].content)

    def test_paragraphs_and_short_documents_are_retained(self):
        text = "Summary.\n\nA second paragraph."
        self.assertEqual(
            DocumentChunker(ChunkingConfig()).chunk_document(text)[0].content, text
        )

    def test_invalid_sizes_fail_at_settings_load(self):
        for options in (
            {"CHUNK_OVERLAP": 300},
            {"CHUNK_SIZE": 0},
            {"MIN_CHUNK_SIZE": 400},
            {"LOCAL_EMBEDDING_BATCH_SIZE": 0},
            {"INFERENCE_QUEUE_TIMEOUT_SECONDS": 0},
            {"RETRIEVAL_TOP_K": 101},
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                Settings(
                    _env_file=None,
                    postgres_user="test",
                    postgres_password="test",
                    **{k.lower(): v for k, v in options.items()},
                )

    def test_token_split_preserves_characters_and_actual_model_limit(self):
        provider = LocalEmbeddingProvider.__new__(LocalEmbeddingProvider)
        provider.model = SimpleNamespace(
            max_seq_length=32,
            tokenizer=SimpleNamespace(
                encode=lambda text, **kwargs: [0] + list(text.encode("utf-8")) + [1]
            ),
        )
        text = "Technical Δοκιμή evidence. " * 30
        pieces = provider.split_text(text)
        self.assertEqual("".join(pieces), text)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(
            all(len(provider.model.tokenizer.encode(p)) <= 32 for p in pieces)
        )

    def test_publication_recency_is_monotonic_and_falls_back(self):
        now = datetime.now(timezone.utc)
        r = Retriever(None, None, RetrievalConfig())
        boosts = [
            r._calculate_recency_boost(
                {"published_at": (now - timedelta(days=d)).isoformat()}, now
            )
            for d in (0, 29, 30, 89, 90, 179, 180, 365, 1000)
        ]
        self.assertEqual(boosts, sorted(boosts, reverse=True))
        self.assertEqual(boosts[0], 1.5)
        self.assertEqual(r._calculate_recency_boost({}, now), 1.0)
        self.assertEqual(
            r._calculate_recency_boost(
                {"published_at": (now + timedelta(days=1)).isoformat()}, now
            ),
            1.0,
        )
        self.assertEqual(
            r._calculate_recency_boost(
                {"published_at": "invalid", "last_modified": now.isoformat()}, now
            ),
            1.5,
        )


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_limit_and_cancelled_thread_keep_exclusive_access(self):
        gate = InferenceGate(0.03, 1)
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def running():
            loop.call_soon_threadsafe(started.set)
            release.wait(2)

        first = asyncio.create_task(gate.run(running))
        try:
            await asyncio.wait_for(started.wait(), 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            with self.assertRaises(BusyError):
                await gate.run(lambda: self.fail("must not overlap"))
            self.assertTrue(gate.lock.locked())
        finally:
            release.set()
            await gate.close()
        self.assertFalse(gate.lock.locked())
        self.assertEqual(gate.pending, 0)

    async def test_pending_admission_is_bounded(self):
        gate = InferenceGate(1, 1)
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def running():
            loop.call_soon_threadsafe(started.set)
            release.wait(2)

        first = asyncio.create_task(gate.run(running))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(gate.run(lambda: None))
        try:
            while not gate.pending:
                await asyncio.sleep(0)
            with self.assertRaises(BusyError):
                await gate.run(lambda: None)
        finally:
            release.set()
            await asyncio.gather(first, second)
            await gate.close()

    async def test_query_can_run_between_embedding_batches(self):
        provider = LocalEmbeddingProvider.__new__(LocalEmbeddingProvider)
        provider.gate = InferenceGate(1)
        provider.batch_size = 1
        provider.normalize = True
        started = asyncio.Event()
        release = threading.Event()
        order = []
        loop = asyncio.get_running_loop()
        vector = SimpleNamespace(tolist=lambda: [1.0, 0.0])

        def encode(texts, **kwargs):
            order.append(texts)
            if texts == ["one"]:
                loop.call_soon_threadsafe(started.set)
                release.wait(2)
            return vector if isinstance(texts, str) else [vector for _ in texts]

        provider.model = SimpleNamespace(encode=encode)
        indexing = asyncio.create_task(provider.embed_batch(["one", "two"]))
        await asyncio.wait_for(started.wait(), 1)
        query = asyncio.create_task(provider.embed_text("question"))
        try:
            while not provider.gate.pending:
                await asyncio.sleep(0)
        finally:
            release.set()
        await asyncio.gather(indexing, query)
        self.assertEqual(order, [["one"], "question", ["two"]])
