"""Regression checks for bounded ingestion, intact text, and model contention."""
import asyncio
import threading
import unittest
from core.providers.base import EmbeddingProvider
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from pydantic import ValidationError

from config.models import ChunkingConfig, RetrievalConfig
from config import Settings
from core.concurrency import BusyError, InferenceGate, document_operation, _document_locks
from core.ingestion.chunker import DocumentChunker
from core.ingestion.scraper import DocumentScraper, PublicResolver, public_url, DownloadError
from core.pipeline.documents import DocumentRequest, DocumentSection, upsert_document
from core.providers.embeddings.local import LocalEmbeddingProvider
from core.retrieval.retriever import Retriever


def embedder():
    return Mock(get_model_name=lambda: 'test', get_dimension=lambda: 2,
                chunking_identity=lambda: {}, prepare_document=EmbeddingProvider.prepare_document.__get__(object()), split_text=lambda text: [text],
                embed_batch=AsyncMock(side_effect=lambda texts: [[1., 0.] for _ in texts]))


class ChunkTests(unittest.TestCase):
    def test_every_word_survives_recursive_and_fixed_boundaries(self):
        words = [f'unique{i}' for i in range(610)]
        variants = [' '.join(words), ' '.join(words[:300])+'. '+ ' '.join(words[300:600])+'. '+ ' '.join(words[600:]),
                    '\n\n'.join(' '.join(words[i:i+60]) for i in range(0,len(words),60))]
        for strategy in ('fixed', 'recursive'):
            for text in variants:
                with self.subTest(strategy=strategy, ending=text[-20:]):
                    chunks = DocumentChunker(ChunkingConfig(strategy=strategy)).chunk_document(text)
                    output = {word.rstrip('.') for c in chunks for word in c.content.split()}
                    self.assertTrue(set(words).issubset(output))
                    self.assertLessEqual(max(c.word_count for c in chunks), 500)
                    self.assertIn('unique609', chunks[-1].content)

    def test_paragraphs_and_short_documents_are_retained(self):
        text='Summary.\n\nA second paragraph.'
        self.assertEqual(DocumentChunker(ChunkingConfig()).chunk_document(text)[0].content, text)

    def test_invalid_sizes_fail_at_settings_load(self):
        for options in ({'CHUNK_OVERLAP':300}, {'CHUNK_SIZE':0}, {'MIN_CHUNK_SIZE':400},
                        {'LOCAL_EMBEDDING_BATCH_SIZE':0}, {'INFERENCE_QUEUE_TIMEOUT_SECONDS':0}, {'RETRIEVAL_TOP_K':101}):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                Settings(_env_file=None, POSTGRES_USER='test', POSTGRES_PASSWORD='test', **options)

    def test_token_split_preserves_characters_and_actual_model_limit(self):
        provider=LocalEmbeddingProvider.__new__(LocalEmbeddingProvider)
        provider.model=SimpleNamespace(max_seq_length=32, tokenizer=SimpleNamespace(
            encode=lambda text, **kwargs: [0]+list(text.encode('utf-8'))+[1]))
        text='Technical Δοκιμή evidence. '*30
        pieces=provider.split_text(text)
        self.assertEqual(''.join(pieces),text)
        self.assertGreater(len(pieces),1)
        self.assertTrue(all(len(provider.model.tokenizer.encode(p)) <= 32 for p in pieces))

    def test_publication_recency_is_monotonic_and_falls_back(self):
        now=datetime.now(timezone.utc)
        r=Retriever(None,None,RetrievalConfig())
        boosts=[r._calculate_recency_boost({'published_at':(now-timedelta(days=d)).isoformat()},now)
                for d in (0,29,30,89,90,179,180,365,1000)]
        self.assertEqual(boosts,sorted(boosts,reverse=True))
        self.assertEqual(boosts[0],1.5)
        self.assertEqual(r._calculate_recency_boost({},now),1.)
        self.assertEqual(r._calculate_recency_boost({'published_at':(now+timedelta(days=1)).isoformat()},now),1.)
        self.assertEqual(r._calculate_recency_boost({'published_at':'invalid','last_modified':now.isoformat()},now),1.5)


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_limit_and_cancelled_thread_keep_exclusive_access(self):
        gate=InferenceGate(.03,1)
        started=asyncio.Event(); release=threading.Event(); loop=asyncio.get_running_loop()
        def running():
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
        first=asyncio.create_task(gate.run(running))
        try:
            await asyncio.wait_for(started.wait(),1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError): await first
            with self.assertRaises(BusyError): await gate.run(lambda: self.fail('must not overlap'))
            self.assertTrue(gate.lock.locked())
        finally:
            release.set()
            await gate.close()
        self.assertFalse(gate.lock.locked())
        self.assertEqual(gate.pending,0)

    async def test_pending_admission_is_bounded(self):
        gate=InferenceGate(1,1)
        started=asyncio.Event(); release=threading.Event(); loop=asyncio.get_running_loop()
        def running():
            loop.call_soon_threadsafe(started.set); release.wait(2)
        first=asyncio.create_task(gate.run(running))
        await asyncio.wait_for(started.wait(),1)
        second=asyncio.create_task(gate.run(lambda: None))
        try:
            while not gate.pending: await asyncio.sleep(0)
            with self.assertRaises(BusyError): await gate.run(lambda: None)
        finally:
            release.set(); await asyncio.gather(first,second); await gate.close()

    async def test_query_can_run_between_embedding_batches(self):
        provider=LocalEmbeddingProvider.__new__(LocalEmbeddingProvider)
        provider.gate=InferenceGate(1); provider.batch_size=1; provider.normalize=True
        started=asyncio.Event(); release=threading.Event(); order=[]; loop=asyncio.get_running_loop()
        vector=SimpleNamespace(tolist=lambda:[1.,0.])
        def encode(texts, **kwargs):
            order.append(texts)
            if texts==['one']:
                loop.call_soon_threadsafe(started.set); release.wait(2)
            return vector if isinstance(texts,str) else [vector for _ in texts]
        provider.model=SimpleNamespace(encode=encode)
        indexing=asyncio.create_task(provider.embed_batch(['one','two']))
        await asyncio.wait_for(started.wait(),1)
        query=asyncio.create_task(provider.embed_text('question'))
        try:
            while not provider.gate.pending: await asyncio.sleep(0)
        finally:
            release.set()
        await asyncio.gather(indexing,query)
        self.assertEqual(order,[['one'],'question',['two']])

    async def test_document_wait_is_bounded_and_lock_entries_are_cleaned(self):
        async with document_operation('c','1'):
            with self.assertRaises(BusyError):
                async with document_operation('c','1',timeout=.01): self.fail()
        self.assertNotIn(('c','1'),_document_locks)

    async def test_concurrent_identical_delivery_embeds_only_once(self):
        status={}
        async def current(*args): return status or None
        async def publish(collection,id,revision,chunks,*,embedding_revision):
            status.update(revision=revision,embedding_revision=embedding_revision,chunks=len(chunks)); return True
        store=Mock(get_document_status=current,replace_document=publish)
        embed=embedder(); doc=DocumentRequest(content='Evidence.')
        results=await asyncio.gather(*(upsert_document(store,embed,ChunkingConfig(),'c','1',doc) for _ in range(3)))
        self.assertEqual(embed.embed_batch.await_count,1)
        self.assertEqual(sum(r['changed'] for r in results),1)

    async def test_token_splits_are_used_with_source_references(self):
        store=Mock(get_document_status=AsyncMock(return_value=None),replace_document=AsyncMock(return_value=True))
        embed=embedder(); embed.split_text=lambda text:[text[:4],text[4:]]
        await upsert_document(store,embed,ChunkingConfig(),'c','1',DocumentRequest(content='Unused',
            sections=[DocumentSection(ref='page 9',text='Eight characters')]))
        chunks=store.replace_document.call_args.args[-1]
        self.assertEqual(''.join(c.content for c in chunks),'Eight characters')
        self.assertTrue(all(c.metadata['section_ref']=='page 9' for c in chunks))


class FakeResponse:
    def __init__(self, body=b'<main>Evidence.</main>',status=200,headers=None):
        self.status=status; self.body=body
        self.headers=headers or {'Content-Type':'text/html'}
        self.content_length=None; self.charset='utf-8'; self.content=self
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    def raise_for_status(self):
        if self.status>=400: raise ValueError('HTTP failure')
    async def iter_chunked(self,size):
        for i in range(0,len(self.body),8): yield self.body[i:i+8]


class ScraperTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_schemes_credentials_and_noncanonical_ips_rejected(self):
        for url in ('file:///tmp/a','ftp://example.org/a','http://localhost','http://127.0.0.1',
                    'http://169.254.169.254','http://224.0.0.1','http://[fec0::1]','http://[::1]','http://0177.0.0.1','http://2130706433',
                    'https://example.org:8080','http://user:pass@example.org','http://%31%32%37.0.0.1'):
            with self.subTest(url=url), self.assertRaises(ValueError): public_url(url)
        self.assertEqual(public_url('https://example.org/a#part'),'https://example.org/a')

    async def test_resolver_pins_and_rejects_mixed_private_addresses(self):
        resolver=PublicResolver()
        try:
            with patch.object(resolver.resolver,'resolve',AsyncMock(return_value=[{'host':'8.8.8.8'},{'host':'127.0.0.1'}])):
                with self.assertRaises(ValueError): await resolver.resolve('example.org',443)
            addresses=[{'host':'8.8.8.8','port':443}]
            with patch.object(resolver.resolver,'resolve',AsyncMock(return_value=addresses)):
                self.assertIs(await resolver.resolve('example.org',443),addresses)
        finally: await resolver.close()

    async def test_stream_size_redirect_destination_and_redirect_count_are_bounded(self):
        cases=[([FakeResponse(b'x'*40)],'limit'),
               ([FakeResponse(status=302,headers={'Location':'http://127.0.0.1/private'})],'public'),
               ([FakeResponse(status=302,headers={'Location':'/next'}) for _ in range(6)],'redirect'),
               ([FakeResponse(headers={'Content-Type':'application/pdf'})],'HTML')]
        for responses,message in cases:
            scraper=DocumentScraper(); get=Mock(side_effect=responses)
            session=SimpleNamespace(get=get)
            with self.subTest(message=message), patch.object(scraper,'_get_session',AsyncMock(return_value=session)), patch('core.ingestion.scraper.MAX_DOWNLOAD_BYTES',32):
                with self.assertRaisesRegex(ValueError,message): await scraper.scrape_url('https://example.org/')
                self.assertTrue(all(call.kwargs['allow_redirects'] is False for call in get.call_args_list))

    async def test_one_fetch_per_page_depth_exclusion_and_short_text(self):
        pages={'https://example.org/':b'<main>A</main><a href="/child">child</a><a href="/skip">skip</a>',
               'https://example.org/child':b'<main>B</main><a href="/grandchild">next</a>'}
        calls=[]
        async def download(url):
            calls.append(url); return pages[url],url,{'content-type':'text/html'},'utf-8'
        scraper=DocumentScraper()
        with patch.object(scraper,'_download',download):
            docs=await scraper.scrape_recursive('https://example.org',max_depth=2,exclude_patterns=['*/skip'])
        self.assertEqual(calls,['https://example.org/','https://example.org/child'])
        self.assertEqual([d.content for d in docs],['A','B'])

    async def test_failures_and_nonmatching_pages_count_against_budget(self):
        calls=[]
        async def scrape(url):
            calls.append(url)
            if url.endswith('bad'): raise ValueError('failed')
            return SimpleNamespace(url=url,links=['https://example.org/bad','https://example.org/later'])
        with patch.object(DocumentScraper,'scrape_url',side_effect=scrape):
            docs=await DocumentScraper().scrape_recursive('https://example.org',max_depth=3,max_pages=2,url_patterns=['*/later'])
        self.assertEqual(len(calls),2)
        self.assertEqual(len(docs),1)

    async def test_download_and_total_crawl_deadlines(self):
        class SlowResponse(FakeResponse):
            async def __aenter__(self): await asyncio.sleep(1)
        scraper=DocumentScraper(timeout=.01,crawl_timeout=.01)
        with patch.object(scraper,'_get_session',AsyncMock(return_value=SimpleNamespace(get=lambda *a,**k:SlowResponse()))):
            with self.assertRaises(DownloadError): await scraper.scrape_url('https://example.org')
        async def slow(url): await asyncio.sleep(1)
        with patch.object(scraper,'scrape_url',slow):
            self.assertEqual(await scraper.scrape_recursive('https://example.org'),[])
