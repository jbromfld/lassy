"""Opt-in integration tests; only a uniquely named disposable database is changed.

Run RAG_TEST_POSTGRES=1 POSTGRES_HOST=127.0.0.1 DEBUG=false python -m unittest discover -s tests -v.
The configured user must be allowed to create databases and pgvector extensions.
Set RAG_TEST_MODEL=1 as well to test the API and CLI with the cached embedding model.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import httpx

from api import main
from config import get_settings
from core.metrics import MetricsTracker
from core.providers.base import Chunk
from core.providers.vector_store.pgvector import PgVectorStore

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv('RAG_TEST_POSTGRES') == '1', 'set RAG_TEST_POSTGRES=1 for disposable-database integration tests')
class PostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = get_settings()
        self.connection = dict(host=self.settings.postgres_host, port=self.settings.postgres_port,
            user=self.settings.postgres_user, password=self.settings.postgres_password)
        self.name = 'rag_retrieval_test_' + uuid4().hex
        self.admin = await asyncpg.connect(**self.connection, database=self.settings.postgres_db)
        self.created = False
        self.pool = None
        self.addAsyncCleanup(self.cleanup)
        await self.admin.execute(f'CREATE DATABASE "{self.name}"')
        self.created = True
        self.pool = await asyncpg.create_pool(**self.connection, database=self.name, min_size=1, max_size=3)
        async with self.pool.acquire() as conn:
            await conn.execute((ROOT/'db/init.sql').read_text())
        self.store = PgVectorStore(self.settings.vector_store_config, self.pool)
        self.tracker = MetricsTracker(self.pool)

    async def cleanup(self):
        if self.pool: await self.pool.close()
        if self.created: await self.admin.execute(f'DROP DATABASE "{self.name}" WITH (FORCE)')
        await self.admin.close()

    async def test_initialization_search_deduplication_and_feedback(self):
        async with self.pool.acquire() as conn:
            self.assertIsNone(await conn.fetchval("SELECT to_regclass('public.configuration_profiles')"))
            columns = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name IN ('embeddings', 'queries')")
            self.assertNotIn('profile_id', [row['column_name'] for row in columns])
            self.assertNotIn('config_snapshot', [row['column_name'] for row in columns])
        chunk_id = uuid4()
        vector = [1.0] + [0.0]*767
        await self.store.insert_chunk(Chunk(id=chunk_id, content="The user's guide: how does PostgreSQL retrieval work?",
            content_hash='integration-hash', vector=vector,
            metadata={"author'": "O'Reilly", 'active': True, 'count': 3}))
        await self.store.insert_chunk(Chunk(id=uuid4(), content="The user's guide: how does PostgreSQL retrieval work?",
            content_hash='integration-hash', vector=vector,
            metadata={"author'": "O'Reilly", 'active': True, 'count': 3}))
        self.assertEqual(await self.store.count(), 1)
        for search in ('vector', 'keyword', 'hybrid'):
            args = dict(top_k=5, filter_dict={"author'": ["O'Reilly"], 'active': True, 'count': 3})
            if search == 'vector': results = await self.store.vector_search(vector, **args)
            elif search == 'keyword': results = await self.store.keyword_search("user's guide?", **args)
            else: results = await self.store.hybrid_search("user's guide?", vector, **args)
            self.assertEqual([r.chunk.id for r in results], [chunk_id])
        self.assertEqual(await self.store.count({"author'": "O'Reilly"}), 1)
        self.assertEqual(await self.store.count({"author'": "x' OR true --"}), 0)
        query_id = uuid4()
        chunks = [{'chunk_id': str(chunk_id), 'content': 'guide', 'score': .4}]
        await self.tracker.save_retrieval(query_id, 'guide', chunks, 12.)
        await self.tracker.save_feedback(query_id, 9, 'helpful')
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT retrieved_chunks FROM queries WHERE query_id=$1', query_id), [chunk_id])
            self.assertEqual(await conn.fetchval('SELECT num_feedback FROM embeddings WHERE id=$1', chunk_id), 1)
            self.assertEqual(await conn.fetchval('SELECT retrieval_count FROM embeddings WHERE id=$1', chunk_id), 1)
            await conn.execute("""CREATE FUNCTION reject_metrics() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'test failure'; END; $$ LANGUAGE plpgsql;
                CREATE TRIGGER reject_metrics BEFORE INSERT ON metrics FOR EACH ROW EXECUTE FUNCTION reject_metrics();""")
        failed_id = uuid4()
        with self.assertRaises(asyncpg.PostgresError):
            await self.tracker.save_retrieval(failed_id, 'guide', chunks, 12.)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM queries WHERE query_id=$1', failed_id), 0)
            self.assertEqual(await conn.fetchval('SELECT retrieval_count FROM embeddings WHERE id=$1', chunk_id), 1)
        summary = await self.tracker.get_metrics_summary()
        self.assertEqual(summary['total_queries'], 1)
        self.assertEqual(summary['avg_feedback_score'], 9)
        self.assertEqual(await self.store.delete_by_filter({"author'": "O'Reilly"}), 1)

    async def test_document_atomic_replacement_identity_and_delete(self):
        from core.pipeline.documents import DocumentRequest, upsert_document
        from unittest.mock import Mock, AsyncMock
        embed = Mock(embed_batch=AsyncMock(side_effect=lambda texts: [[1.] + [0.]*767 for _ in texts]),
                     get_model_name=lambda: 'test', get_dimension=lambda: 768)
        config = self.settings.chunking_config
        doc = DocumentRequest(content='VLM evidence.', title='Paper')
        first = await upsert_document(self.store, embed, config, 'corpus', '1', doc)
        again = await upsert_document(self.store, embed, config, 'corpus', '1', doc)
        self.assertTrue(first['changed'])
        self.assertFalse(again['changed'])
        self.assertEqual(again['embeddings_generated'], 0)
        self.assertEqual(embed.embed_batch.await_count, 1)
        await upsert_document(self.store, embed, config, 'other', '1', doc)
        self.assertEqual(await self.store.count(), 2)
        async with self.pool.acquire() as conn:
            await conn.execute("""CREATE FUNCTION reject_document() RETURNS trigger AS $$
                BEGIN IF NEW.content='Rejected change.' THEN RAISE EXCEPTION 'rejected'; END IF;
                RETURN NEW; END; $$ LANGUAGE plpgsql;
                CREATE TRIGGER reject_document BEFORE INSERT ON embeddings
                FOR EACH ROW EXECUTE FUNCTION reject_document();""")
        with self.assertRaises(asyncpg.PostgresError):
            await upsert_document(self.store, embed, config, 'corpus', '1', DocumentRequest(content='Rejected change.'))
        self.assertEqual(await self.store.count(), 2)
        rows = await self.store.hybrid_search('VLM', [1.]+[0.]*767, filter_dict={'collection':'corpus'})
        self.assertEqual([r.chunk.content for r in rows], ['VLM evidence.'])
        await upsert_document(self.store, embed, config, 'corpus', '1', DocumentRequest(content='New finding.'))
        rows = await self.store.vector_search([1.]+[0.]*767, filter_dict={'collection':'corpus'})
        self.assertEqual([r.chunk.content for r in rows], ['New finding.'])
        await self.store.delete_document('corpus', '1')
        await self.store.delete_document('corpus', '1')
        self.assertEqual(await self.store.count(), 1)
        async with self.pool.acquire() as conn:
            await conn.execute((ROOT/'db/002_documents.sql').read_text())
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM documents'), 1)

    async def test_explicit_schema_reset(self):
        async with self.pool.acquire() as conn:
            await conn.execute("CREATE MATERIALIZED VIEW metrics_by_config AS SELECT count(*) FROM queries")
            await conn.execute((ROOT/'db/cleanup.sql').read_text())
            await conn.execute((ROOT/'db/init.sql').read_text())
            self.assertIsNone(await conn.fetchval("SELECT to_regclass('public.configuration_profiles')"))
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM queries"), 0)

    @unittest.skipUnless(os.getenv('RAG_TEST_MODEL') == '1', 'set RAG_TEST_MODEL=1 for live API/CLI model test')
    async def test_standalone_api_and_cli_with_no_copilot_key(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'POSTGRES_DB': self.name, 'POSTGRES_HOST': self.settings.postgres_host,
            'POSTGRES_PORT': str(self.settings.postgres_port), 'POSTGRES_USER': self.settings.postgres_user,
            'POSTGRES_PASSWORD': self.settings.postgres_password, 'COPILOT_API_KEY': '', 'DEBUG': 'false',
            'RETRIEVAL_TOP_K': '2', 'CHUNK_SIZE': '200', 'ENABLE_RERANKING': 'false',
            'HF_HUB_OFFLINE': '1', 'RAG_SERVICE_URL': f'http://127.0.0.1:{port}'}
        # Keep subprocess output off the terminal (connection exceptions can include sensitive context).
        import tempfile
        with tempfile.TemporaryFile(mode='w+') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                async with httpx.AsyncClient(base_url=env['RAG_SERVICE_URL'], timeout=90) as client:
                    deadline = time.monotonic() + 90
                    while True:
                        if process.poll() is not None:
                            log.seek(0)
                            # Local model errors only; never print full logs/credentials.
                            output = log.read()
                            message = output[-1200:].replace(self.settings.postgres_password, '<redacted>')
                            self.fail('API exited during startup: ' + message)
                        try:
                            response = await client.get('/health')
                            if response.status_code == 200: break
                        except httpx.ConnectError: pass
                        if time.monotonic() > deadline: self.fail('API readiness timeout')
                        await asyncio.sleep(.25)
                    self.assertEqual((await client.get('/report')).status_code, 404)
                    self.assertEqual((await client.get('/profiles')).status_code, 404)
                    self.assertEqual((await client.get('/metrics')).status_code, 200)
                    ingestion = await client.post('/ingest', json={'title': 'Standalone retrieval guide', 'content': ('Standalone retrieval uses PostgreSQL pgvector and local embeddings. No Copilot API key or VS Code process is required. ' * 12)})
                    self.assertEqual(ingestion.status_code, 200, ingestion.text)
                    self.assertGreater(ingestion.json()['chunks_created'], 0)
                    proc = await asyncio.create_subprocess_exec(sys.executable, 'cli.py', 'query', 'How does standalone retrieval work?', cwd=ROOT, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), 90)
                    self.assertEqual(proc.returncode, 0, stderr.decode())
                    payload = json.loads(stdout.decode().split('\nStatus:')[0])
                    self.assertGreater(len(payload['chunks']), 0)
                    self.assertTrue(payload['feedback_available'])
                    self.assertLessEqual(len(payload['chunks']), 2)
                    self.assertEqual(payload['chunks'][0]['citation'], '[1]')
                    self.assertEqual(payload['chunks'][0]['url'], '')
                    feedback = await client.post('/feedback', json={'query_id': payload['query_id'], 'score': 9})
                    self.assertEqual(feedback.status_code, 200, feedback.text)
            finally:
                process.terminate()
                await asyncio.to_thread(process.wait, 15)


if __name__ == '__main__': unittest.main()
