import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from config.models import ChunkingConfig
from core.pipeline.documents import DocumentRequest, DocumentSection, upsert_document
from core.pipeline.ingestion_pipeline import IngestionPipeline, IngestionRequest


def embedder():
    return Mock(embed_batch=AsyncMock(side_effect=lambda texts: [[1., 0.] for _ in texts]),
                get_model_name=lambda: 'test-model', get_dimension=lambda: 2)


class DocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_sections_have_stable_scoped_identity(self):
        store = Mock(get_document_status=AsyncMock(return_value=None), replace_document=AsyncMock(return_value=True))
        document = DocumentRequest(content='Evidence', sections=[
            DocumentSection(ref='page 1', text='Short finding.'),
            DocumentSection(ref='page 2', text='Short finding.')])
        await upsert_document(store, embedder(), ChunkingConfig(), 'corpus', '1', document)
        first = store.replace_document.call_args.args[-1]
        self.assertEqual(len(first), 2)
        self.assertEqual([c.metadata['section_ref'] for c in first], ['page 1','page 2'])
        self.assertNotEqual(first[0].content_hash, first[1].content_hash)
        await upsert_document(store, embedder(), ChunkingConfig(), 'corpus', '1', document)
        self.assertEqual(first, store.replace_document.call_args.args[-1])
        await upsert_document(store, embedder(), ChunkingConfig(), 'other', '1', document)
        self.assertNotEqual(first[0].content_hash, store.replace_document.call_args.args[-1][0].content_hash)

    async def test_embedding_failure_does_not_publish(self):
        store = Mock(get_document_status=AsyncMock(return_value=None), replace_document=AsyncMock())
        embed = embedder()
        embed.embed_batch.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            await upsert_document(store, embed, ChunkingConfig(), 'rss', '1', DocumentRequest(content='Short summary'))
        store.replace_document.assert_not_called()
        with self.assertRaises(ValidationError):
            DocumentRequest(content='  ')

    async def test_legacy_ingestion_uses_same_pipeline(self):
        store = Mock(get_document_status=AsyncMock(return_value=None), replace_document=AsyncMock(return_value=True))
        pipeline = IngestionPipeline(store, embedder(), ChunkingConfig())
        try:
            result = await pipeline.ingest(IngestionRequest(content='Short RSS summary.', title='VLM'))
            self.assertTrue(result.success, result.error_message)
            self.assertEqual(result.chunks_created, 1)
            self.assertEqual(store.replace_document.call_args.args[0], 'direct')
            doc = SimpleNamespace(url='https://example.org', content='Page content.', title='Web', metadata={})
            with patch.object(pipeline.scraper, 'scrape_url', AsyncMock(return_value=doc)):
                result = await pipeline.ingest(IngestionRequest(url=doc.url))
                self.assertTrue(result.success, result.error_message)
                self.assertEqual(store.replace_document.call_args.args[0], 'web')
        finally:
            await pipeline.close()


class EvaluationTests(unittest.TestCase):
    def test_scores_distinct_document_coverage(self):
        from evaluate import score
        chunks = [{'collection':'c', 'document_id':id} for id in ('a','a','b','c')]
        result = score(chunks, [{'collection':'c','document_id':'b'}, {'collection':'c','document_id':'missing'}])
        self.assertEqual(result['recall'], .5)
        self.assertEqual(result['reciprocal_rank'], .5)
        self.assertEqual(len(result['retrieved']), 3)
