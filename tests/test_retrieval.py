import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx

from api import main
from config import Settings
from core.metrics import MetricsTracker
from core.providers.factory import ProviderFactory
from core.providers.vector_store.pgvector import PgVectorStore


def settings():
    return Settings(_env_file=None, POSTGRES_USER="test", POSTGRES_PASSWORD="test", DEBUG=False)


class Pool:
    def __init__(self, conn):
        self.conn = conn
        self.close = AsyncMock()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = settings()
        self.factory = MagicMock()
        self.factory.get_embedding_provider = AsyncMock()
        self.factory.get_reranker = AsyncMock(return_value=None)
        self.factory.close = AsyncMock()
        self.tracker = MagicMock()
        self.tracker.save_retrieval = AsyncMock()
        self.patches = [
            patch.object(main.app_state, 'settings', self.settings),
            patch.object(main.app_state, 'provider_factory', self.factory),
            patch.object(main.app_state, 'metrics_tracker', self.tracker),
        ]
        for p in self.patches: p.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches): p.stop()

    async def query(self, results, **body):
        with patch('api.main.Retriever') as retriever:
            retriever.return_value.retrieve = AsyncMock(return_value=results)
            retriever.return_value.rerank_time_ms = 0.0
            return await self.client.post('/query', json={'query': 'How does it work?', **body})

    async def test_default_and_explicit_retrieval_preserve_chunks(self):
        ids = [str(uuid4()) for _ in range(3)]
        results = [SimpleNamespace(chunk_id=id, content=f'content {i}', boosted_score=.5,
            rerank_score=None, metadata={'title': 'Doc', 'source_url': 'https://example.com'} if i < 2 else {}) for i, id in enumerate(ids)]
        for body in ({}, {'retrieve_only': True}):
            response = await self.query(results, **body)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual([c['citation'] for c in data['chunks']], ['[1]', '[2]', '[3]'])
            self.assertEqual([c['chunk_id'] for c in data['chunks']], ids)
            self.assertEqual(data['chunks'][2]['url'], '')
            self.assertTrue(data['feedback_available'])
        self.assertFalse(hasattr(ProviderFactory, "create_llm_provider"))

    async def test_empty_results_and_failed_tracking(self):
        self.assertEqual((await self.query([])).json()['chunks'], [])
        self.tracker.save_retrieval.side_effect = RuntimeError('tracking unavailable')
        data = (await self.query([])).json()
        self.assertFalse(data['feedback_available'])
        self.assertIn('query_id', data)

    async def test_input_validation(self):
        for body in ({'query': ''}, {'query': '  '}, {'top_k': 0}, {'top_k': -1}):
            self.assertEqual((await self.query([], **body)).status_code, 422)

    async def test_document_api_and_provenance(self):
        store = self.factory.create_vector_store.return_value
        store.get_document_status = AsyncMock(return_value=None)
        store.replace_document = AsyncMock(return_value=True)
        store.delete_document = AsyncMock()
        embed = MagicMock()
        self.factory.get_embedding_provider.return_value = embed
        embed.get_model_name.return_value = 'test'
        embed.get_dimension.return_value = 768
        embed.embed_batch = AsyncMock(return_value=[[1.] + [0.] * 767])
        response = await self.client.put('/documents/rss/123', json={'content':'Short summary.', 'title':'VLM'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['chunks_created'], 1)
        self.assertEqual(store.replace_document.call_args.args[:2], ('rss', '123'))
        response = await self.client.delete('/documents/rss/123')
        self.assertEqual(response.status_code, 200)
        store.delete_document.assert_awaited_once_with('rss', '123')
        response = await self.client.put('/documents/rss/123', json={'content':'  '})
        self.assertEqual(response.status_code, 422)
        data = (await self.query([SimpleNamespace(chunk_id='id',content='Evidence',boosted_score=.1,
            rerank_score=None,metadata={'collection':'corpus','document_id':'1','section_ref':'page 2'})])).json()
        self.assertEqual(data['chunks'][0]['section'], 'page 2')
        self.assertEqual(data['chunks'][0]['document_id'], '1')

    async def test_generation_is_rejected(self):
        response = await self.client.post('/query', json={'query': 'test', 'retrieve_only': False})
        self.assertEqual(response.status_code, 422)

    async def test_health_unavailable(self):
        with patch.object(main, 'check_readiness', AsyncMock(side_effect=RuntimeError('db unavailable'))):
            self.assertEqual((await self.client.get('/health')).status_code, 503)
    async def test_profiles_removed_and_environment_defaults_used(self):
        for endpoint in ('/profiles', '/report'):
            self.assertEqual((await self.client.get(endpoint)).status_code, 404)
        schema = main.app.openapi()
        for name in ('IngestRequest', 'QueryRequestAPI'):
            self.assertNotIn('profile', schema['components']['schemas'][name]['properties'])
        self.settings.retrieval_top_k = 2
        with patch('api.main.Retriever') as retriever:
            retriever.return_value.retrieve = AsyncMock(return_value=[])
            retriever.return_value.rerank_time_ms = 0.0
            response = await self.client.post('/query', json={'query': 'question'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('profile', response.json()['metrics'])
            self.assertEqual(retriever.return_value.retrieve.await_args.kwargs['top_k'], 2)
            await self.client.post('/query', json={'query': 'question', 'top_k': 3})
            self.assertEqual(retriever.return_value.retrieve.await_args.kwargs['top_k'], 3)



class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_cache_reuses_configuration(self):
        factory = ProviderFactory()
        provider = MagicMock(close=AsyncMock())
        with patch.object(factory, 'create_embedding_provider', return_value=provider) as create:
            config = settings().embedding_config
            values = await asyncio.gather(*(factory.get_embedding_provider(config.model_copy()) for _ in range(3)))
            self.assertTrue(all(v is provider for v in values))
            create.assert_called_once()
        await factory.close()
        provider.close.assert_awaited_once()

    async def test_startup_failure_and_shutdown_close_resources(self):
        for failure in (None, RuntimeError('bad schema')):
            pool = Pool(MagicMock())
            factory = MagicMock(close=AsyncMock())
            with patch.object(main, 'get_settings', return_value=settings()), patch.object(main.asyncpg, 'create_pool', AsyncMock(return_value=pool)), patch.object(main, 'ProviderFactory', return_value=factory), patch.object(main, 'check_readiness', AsyncMock(side_effect=failure)):
                if failure:
                    with self.assertRaisesRegex(RuntimeError, 'bad schema'):
                        async with main.lifespan(main.app): pass
                else:
                    async with main.lifespan(main.app): pass
                pool.close.assert_awaited_once()
                factory.close.assert_awaited_once()
                self.assertIsNone(main.app_state.db_pool)

    async def test_readiness_schema_and_dimensions(self):
        conn = MagicMock(execute=AsyncMock())
        with patch.object(main.app_state, 'db_pool', Pool(conn)), patch.object(main.app_state, 'settings', settings()):
            conn.fetchval = AsyncMock(side_effect=[1, None])
            with self.assertRaisesRegex(RuntimeError, 'Missing table'):
                await main.check_readiness()
            conn.fetchval = AsyncMock(side_effect=[1, *([True]*5), False, 'vector(384)'])
            with self.assertRaisesRegex(RuntimeError, 'dimension'):
                await main.check_readiness()
            conn.fetchval = AsyncMock(side_effect=[1, *([True]*5), True])
            with self.assertRaisesRegex(RuntimeError, 'Outdated schema'):
                await main.check_readiness()
            conn.fetchval = AsyncMock(side_effect=[1, *([True]*5), False, 'vector(768)'])
            await main.check_readiness()

    async def test_model_dimension_mismatch_and_embedding_worker(self):
        import threading
        from core.providers.embeddings.local import LocalEmbeddingProvider
        config = settings().embedding_config
        with patch('core.providers.embeddings.local.SentenceTransformer') as model:
            model.return_value.encode.return_value = [0.] * 384
            with self.assertRaisesRegex(ValueError, 'configured 768'):
                LocalEmbeddingProvider(config)
        provider = LocalEmbeddingProvider.__new__(LocalEmbeddingProvider)
        provider._encode_lock = threading.Lock()
        provider.normalize = True
        event_loop_thread = threading.get_ident()
        def encode(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), event_loop_thread)
            return SimpleNamespace(tolist=lambda: [1., 0.])
        provider.model = MagicMock(encode=encode)
        self.assertEqual(await provider.embed_text('query'), [1., 0.])



class TrackingTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_tracking_and_ids(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock()
        transaction.__aexit__ = AsyncMock(return_value=False)
        conn.transaction.return_value = transaction
        tracker = MetricsTracker(Pool(conn))
        chunk = {'chunk_id': str(uuid4()), 'score': .3}
        await tracker.save_retrieval(uuid4(), 'query', [chunk], 12.0)
        self.assertEqual(conn.execute.await_args_list[0].args[-1], [__import__('uuid').UUID(chunk['chunk_id'])])
        transaction.__aexit__.assert_awaited_once_with(None, None, None)
        conn.execute.side_effect = RuntimeError('metrics insert failed')
        with self.assertRaises(RuntimeError):
            await tracker.save_retrieval(uuid4(), 'query', [chunk], 12.0)
        self.assertIs(transaction.__aexit__.await_args.args[0], RuntimeError)

    def test_filters_bind_keys_values_lists_and_booleans(self):
        store = PgVectorStore(settings().vector_store_config, MagicMock())
        filters = {"author'": "O'Reilly", 'tags': ["x' OR true --"], 'active': True, 'count': 3}
        sql, args = store._build_filter_clause(filters, 4)
        self.assertNotIn("O'Reilly", sql)
        self.assertNotIn("author'", sql)
        self.assertIn('$5::text', sql)
        self.assertIn('::boolean', sql)
        self.assertEqual(args, ["author'", "O'Reilly", 'tags', ["x' OR true --"], 'active', True, 'count', 3])




class RerankingTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidates_rerank_then_top_k(self):
        from core.retrieval import Retriever
        from core.providers.base import Chunk, SearchResult
        config = settings().retrieval_config.model_copy(update={'enable_reranking': True, 'rerank_top_k': 3})
        store = MagicMock()
        store.hybrid_search = AsyncMock(return_value=[SearchResult(Chunk(uuid4(), f'chunk {i}', str(i), metadata={}), .5-i*.1, i+1) for i in range(3)])
        embed = MagicMock(embed_text=AsyncMock(return_value=[1.]))
        reranker = MagicMock(score=AsyncMock(return_value=[-2., 3., 1.]))
        retriever = Retriever(store, embed, config, reranker)
        results = await retriever.retrieve('question', top_k=1)
        self.assertEqual(results[0].content, 'chunk 1')
        self.assertEqual(results[0].rerank_score, 3.)
        self.assertEqual(results[0].rank, 1)
        reranker.score.assert_awaited_once_with('question', ['chunk 0', 'chunk 1', 'chunk 2'])
        self.assertEqual(store.hybrid_search.await_args.kwargs['top_k'], 6)
        reranker.score.return_value = [float('nan'), 1., 2.]
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            await retriever.retrieve('question')

    async def test_disabled_reranking_and_empty_results(self):
        from core.retrieval import Retriever
        store = MagicMock(hybrid_search=AsyncMock(return_value=[]))
        embed = MagicMock(embed_text=AsyncMock(return_value=[1.]))
        reranker = MagicMock(score=AsyncMock())
        self.assertEqual(await Retriever(store, embed, settings().retrieval_config, reranker).retrieve('empty'), [])
        reranker.score.assert_not_awaited()

    async def test_local_reranker_runs_off_event_loop(self):
        import threading
        from core.retrieval.reranker import LocalReranker
        scorer = LocalReranker.__new__(LocalReranker)
        scorer._lock = threading.Lock()
        event_loop_thread = threading.get_ident()
        def predict(pairs, **kwargs):
            self.assertNotEqual(threading.get_ident(), event_loop_thread)
            self.assertEqual(pairs, [('question', 'document')])
            return SimpleNamespace(tolist=lambda: [1.5])
        scorer.model = MagicMock(predict=predict)
        self.assertEqual(await scorer.score('question', ['document']), [1.5])
        self.assertEqual(await scorer.score('question', []), [])
        await scorer.close()

    async def test_reranker_cache_and_cleanup(self):
        factory = ProviderFactory()
        config = settings().retrieval_config
        self.assertIsNone(await factory.get_reranker(config, 'cpu'))
        config.enable_reranking = True
        model = MagicMock(close=AsyncMock())
        with patch('core.retrieval.reranker.LocalReranker', return_value=model) as create:
            self.assertIs(await factory.get_reranker(config, 'cpu'), model)
            self.assertIs(await factory.get_reranker(config.model_copy(), 'cpu'), model)
            create.assert_called_once()
        await factory.close()
        model.close.assert_awaited_once()


class SettingsTests(unittest.TestCase):
    def test_environment_and_dotenv_configure_all_components(self):
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('CHUNK_SIZE=200\nRETRIEVAL_TOP_K=2\nENABLE_RERANKING=true\nLOCAL_EMBEDDING_BATCH_SIZE=8\n')
            with patch.dict(os.environ):
                for key in ('ENABLE_RERANKING', 'CHUNK_SIZE', 'LOCAL_EMBEDDING_BATCH_SIZE'):
                    os.environ.pop(key, None)
                os.environ['RETRIEVAL_TOP_K'] = '3'
                config = Settings(_env_file=path, POSTGRES_USER='test', POSTGRES_PASSWORD='test', DEBUG=False)
                self.assertEqual(config.chunking_config.chunk_size, 200)
                self.assertEqual(config.retrieval_config.top_k, 3)
                self.assertTrue(config.retrieval_config.enable_reranking)
                self.assertEqual(config.embedding_config.batch_size, 8)
                self.assertEqual(config.embedding_config.dimension, config.vector_store_config.dimension)



if __name__ == '__main__': unittest.main()
