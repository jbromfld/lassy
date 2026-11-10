"""The HTTP and MCP boundaries share validation and retrieval behavior."""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from lassy.api import create_app
from lassy.concurrency import BusyError
from lassy.mcp import build_server
from lassy.service import Service
from tests.test_core import MemoryStore, embedder, settings


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = Service(settings(), Mock(), embedder())
        self.service.store = MemoryStore()
        self.service.store.hybrid_search = AsyncMock(return_value=[])
        self.service.store.delete_document = AsyncMock()
        self.service.status = AsyncMock(return_value={"status": "healthy"})
        app = create_app(self.service)
        app.state.service = self.service
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        )
        self.server = build_server(self.service)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def mcp(self, name, arguments):
        result = await self.server.call_tool(name, arguments)
        if isinstance(result, tuple):
            return result[1]
        if isinstance(result, list):
            return json.loads(result[0].text)
        return result

    async def test_shared_cited_response_contract(self):
        from types import SimpleNamespace

        expected = json.loads(Path("contracts/retrieval-response.json").read_text())
        chunk = expected["chunks"][0]
        metadata = {
            key: chunk[key]
            for key in (
                "title",
                "collection",
                "document_id",
                "revision",
                "content_type",
                "published_at",
                "last_modified",
            )
        }
        metadata.update(source_url=chunk["url"], section_ref=chunk["section"])
        result = SimpleNamespace(
            chunk_id=chunk["chunk_id"],
            content=chunk["content"],
            boosted_score=chunk["score"],
            rerank_score=chunk["rerank_score"],
            metadata=metadata,
        )
        with (
            patch("lassy.service.Retriever") as retriever,
            patch("lassy.service.time.perf_counter", return_value=1.0),
        ):
            retriever.return_value.retrieve = AsyncMock(return_value=[result])
            retriever.return_value.rerank_time_ms = 0.0
            response = await self.client.post("/query", json={"query": "evidence"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected)
            self.assertEqual(
                await self.mcp("search_knowledge_base", {"query": "evidence"}), expected
            )

    async def test_mutations_health_and_removed_routes(self):
        body = {"content": "Short evidence.", "title": "Research"}
        created = await self.client.put("/documents/c/1", json=body)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["embeddings_generated"], 1)
        retried = await self.mcp(
            "upsert_document", {"collection": "c", "document_id": "1", "document": body}
        )
        self.assertFalse(retried["changed"])
        self.assertEqual(
            (await self.client.get("/health")).json(),
            await self.mcp("lassy_status", {}),
        )
        deleted = await self.client.delete("/documents/c/1")
        self.assertEqual(
            deleted.json(),
            await self.mcp("delete_document", {"collection": "c", "document_id": "1"}),
        )
        for route in ("/ingest", "/feedback", "/metrics"):
            self.assertEqual((await self.client.post(route, json={})).status_code, 404)
        tools = {t.name: t for t in await self.server.list_tools()}
        self.assertEqual(
            set(tools),
            {
                "lassy_status",
                "search_knowledge_base",
                "upsert_document",
                "delete_document",
            },
        )
        self.assertTrue(tools["search_knowledge_base"].annotations.readOnlyHint)
        self.assertTrue(tools["delete_document"].annotations.destructiveHint)

    async def test_limits_safe_errors_and_busy(self):
        invalid = [
            {"query": " "},
            {"query": "x" * 4001},
            {"query": "q", "filters": {"x": {}}},
            {"query": "q", "top_k": 101},
        ]
        for body in invalid:
            self.assertEqual(
                (await self.client.post("/query", json=body)).status_code, 422
            )
            with self.assertRaises(ToolError):
                await self.mcp("search_knowledge_base", body)
        for body in (
            {"content": "x" * 2_000_001},
            {"content": "x", "metadata": {"private": "x" * 32768}},
            {"content": "x", "sections": [{"ref": "p", "text": "x" * 1_000_001}] * 2},
        ):
            response = await self.client.put("/documents/c/1", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertLess(len(response.content), 200)
        with self.assertRaises(ToolError) as error:
            await self.mcp("search_knowledge_base", {"query": ["private-source-text"]})
        self.assertNotIn("private-source-text", str(error.exception))
        self.service.search = AsyncMock(
            side_effect=RuntimeError("private-password-and-source")
        )
        response = await self.client.post("/query", json={"query": "q"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-password", response.text)
        with self.assertRaises(ToolError) as error:
            await self.mcp("search_knowledge_base", {"query": "q"})
        self.assertNotIn("private-password", str(error.exception))
        self.service.search.side_effect = BusyError("private")
        response = await self.client.post("/query", json={"query": "q"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "30")
        with self.assertRaisesRegex(ToolError, "busy:"):
            await self.mcp("search_knowledge_base", {"query": "q"})

        async def large_body():
            for _ in range(3):
                yield b" " * 3_000_000

        response = await self.client.post(
            "/query", content=large_body(), headers={"content-type": "application/json"}
        )
        self.assertEqual(response.status_code, 413)
