"""Opt-in PostgreSQL/process/stdio tests, creating only uniquely named disposable DBs."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lassy.concurrency import BusyError
from lassy.config import Settings
from lassy.documents import DocumentRequest
from lassy.service import Service, index_identity
from lassy.store import PgVectorStore
from tests.process_fixture import SyntheticEmbedder

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("LASSY_TEST_POSTGRES") == "1",
    "Set LASSY_TEST_POSTGRES=1 for isolated PostgreSQL integration",
)
class PostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin_dsn = os.environ.get(
            "LASSY_TEST_ADMIN_DSN", "postgresql:///postgres"
        )
        self.database = "lassy_test_" + uuid4().hex
        self.admin = await asyncpg.connect(self.admin_dsn)
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        parts = urlsplit(self.admin_dsn)
        self.dsn = urlunsplit(
            (parts.scheme, parts.netloc, "/" + self.database, parts.query, "")
        )
        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=1)
            async with self.pool.acquire() as conn:
                await conn.execute((ROOT / "db/init.sql").read_text())
            self.settings = Settings(postgres_user="test", postgres_password="test")
            self.service = Service(self.settings, self.pool, SyntheticEmbedder())
            await self.service.ready(initialize=True)
        except BaseException:
            await self.asyncTearDown()
            raise

    async def asyncTearDown(self):
        if hasattr(self, "pool"):
            await asyncio.wait_for(self.pool.close(), 10)
        await self.admin.execute(f'DROP DATABASE "{self.database}" WITH (FORCE)')
        await self.admin.close()

    async def test_document_search_metadata_and_no_tracking_writes(self):
        doc = DocumentRequest(
            content="Synthetic evidence about local retrieval.",
            title="First",
            metadata={"active": True},
        )
        first = await self.service.upsert("c", "1", doc)
        response = await self.service.search(
            {"query": "evidence", "filters": {"collection": ["c"], "active": True}}
        )
        self.assertTrue(response["chunks"])
        ids = [c["chunk_id"] for c in response["chunks"]]
        second = await self.service.upsert(
            "c", "1", doc.model_copy(update={"title": "Updated"})
        )
        self.assertEqual(second["embeddings_generated"], 0)
        self.assertNotEqual(first["revision"], second["revision"])
        rows = await self.service.search({"query": "evidence"})
        self.assertEqual(ids, [c["chunk_id"] for c in rows["chunks"]])
        self.assertEqual(rows["chunks"][0]["title"], "Updated")
        self.assertEqual(
            (
                await self.service.search(
                    {"query": "evidence", "filters": {"active": "true"}}
                )
            )["chunks"],
            [],
        )
        async with self.pool.acquire() as conn:
            for table in ("queries", "feedback", "metrics"):
                self.assertIsNone(await conn.fetchval("SELECT to_regclass($1)", table))
                await conn.execute(f"CREATE TABLE {table}(sentinel TEXT)")
                await conn.execute(f"INSERT INTO {table} VALUES ('preserved')")
            await conn.execute(
                "ALTER TABLE embeddings ADD COLUMN avg_feedback_score FLOAT, ADD COLUMN num_feedback INT DEFAULT 0, ADD COLUMN retrieval_count INT DEFAULT 0"
            )
        await self.service.search({"query": "evidence"})
        async with self.pool.acquire() as conn:
            for table in ("queries", "feedback", "metrics"):
                self.assertEqual(
                    [
                        dict(row)
                        for row in await conn.fetch("SELECT sentinel FROM " + table)
                    ],
                    [{"sentinel": "preserved"}],
                )
        await self.service.delete("c", "1")
        self.assertEqual(
            (await self.service.search({"query": "evidence"}))["chunks"], []
        )

    async def test_additive_migration_adoption_and_identity_mismatch(self):
        await self.service.upsert(
            "c", "1", DocumentRequest(content="Preserved vectors.")
        )
        async with self.pool.acquire() as conn:
            before = await conn.fetch("SELECT id, content_vector::text FROM embeddings")
            await conn.execute("DROP TABLE lassy_index_identity")
            for _ in range(2):
                await conn.execute((ROOT / "db/004_mcp.sql").read_text())
        with self.assertRaisesRegex(RuntimeError, "unverified"):
            await self.service.ready(initialize=True)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO lassy_index_identity VALUES(TRUE,$1::jsonb,TRUE)",
                json.dumps(index_identity(self.settings, self.service.embedder)),
            )
        await self.service.ready()
        self.assertEqual(
            self.service.embedder.get_model_name(), self.settings.local_embedding_model
        )
        self.settings.local_embedding_revision = "f" * 40
        with self.assertRaisesRegex(RuntimeError, "differs"):
            await self.service.ready()
        async with self.pool.acquire() as conn:
            self.assertEqual(
                await conn.fetch("SELECT id, content_vector::text FROM embeddings"),
                before,
            )

    async def test_lock_timeout_cancel_and_size_one_pool(self):
        other = await asyncpg.create_pool(self.dsn, min_size=1, max_size=1)
        try:
            store = PgVectorStore(other, lock_timeout=0.1)
            async with self.service.store.document_operation("c", "1"):
                with self.assertRaises(BusyError):
                    async with store.document_operation("c", "1"):
                        self.fail("concurrent lock acquired")

                async def waiting():
                    async with PgVectorStore(other).document_operation("c", "1"):
                        self.fail("cancelled waiter acquired lock")

                task = asyncio.create_task(waiting())
                await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            async with store.document_operation("c", "1"):
                pass

            async def cancel_holder():
                async with store.document_operation("c", "1"):
                    asyncio.current_task().cancel()
                    await asyncio.sleep(0)

            with self.assertRaises(asyncio.CancelledError):
                await asyncio.create_task(cancel_holder())
            async with self.service.store.document_operation("c", "1"):
                pass
        finally:
            await asyncio.wait_for(other.close(), 3)

    async def launch(self, action, signals, hold=False):
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.process_fixture",
            action,
            cwd=ROOT,
            env={
                **os.environ,
                "LASSY_TEST_DSN": self.dsn,
                "LASSY_TEST_SIGNALS": signals,
                "LASSY_TEST_HOLD": str(int(hold)),
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def process_race(self, action):
        processes = []
        with tempfile.TemporaryDirectory(prefix="lassy-process-") as signals:
            try:
                first = await self.launch("upsert", signals, hold=True)
                processes.append(first)
                self.assertEqual(
                    await asyncio.wait_for(first.stdout.readline(), 5), b"attempt\n"
                )
                async with asyncio.timeout(5):
                    while not Path(signals, "started").exists():
                        await asyncio.sleep(0.02)
                second = await self.launch(action, signals)
                processes.append(second)
                self.assertEqual(
                    await asyncio.wait_for(second.stdout.readline(), 5), b"attempt\n"
                )
                await asyncio.sleep(0.15)
                self.assertIsNone(second.returncode)
                Path(signals, "release").touch()
                outputs = await asyncio.wait_for(
                    asyncio.gather(*(p.communicate() for p in processes)), 10
                )
                for p, (out, err) in zip(processes, outputs):
                    self.assertEqual(p.returncode, 0, err.decode())
                    json.loads(out)
                self.assertEqual(Path(signals, "embeddings").read_text(), "batch\n")
                if action == "delete":
                    self.assertEqual(await self.service.store.count(), 0)
                else:
                    self.assertFalse(json.loads(outputs[1][0])["changed"])
            finally:
                for p in processes:
                    if p.returncode is None:
                        p.kill()
                        await p.wait()

    async def test_independent_process_delete_cannot_resurrect_document(self):
        await self.process_race("delete")

    async def test_independent_process_retry_embeds_only_once(self):
        await self.process_race("upsert")

    async def test_real_stdio_tools_mutations_and_safe_failures(self):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tests.process_fixture", "stdio"],
            cwd=str(ROOT),
            env={**os.environ, "LASSY_TEST_DSN": self.dsn},
        )
        with tempfile.TemporaryFile(mode="w+") as errors:
            async with (
                stdio_client(params, errlog=errors) as streams,
                ClientSession(*streams) as client,
            ):
                await client.initialize()
                self.assertEqual(len((await client.list_tools()).tools), 4)
                self.assertFalse((await client.call_tool("lassy_status", {})).isError)
                created = await client.call_tool(
                    "upsert_document",
                    {
                        "collection": "stdio",
                        "document_id": "1",
                        "document": {"content": "Synthetic evidence."},
                    },
                )
                self.assertFalse(created.isError)
                result = await client.call_tool(
                    "search_knowledge_base", {"query": "evidence"}
                )
                self.assertTrue(result.structuredContent["chunks"])
                for args in (
                    {"query": "private-error"},
                    {"query": ["PRIVATE_SOURCE"]},
                    {"query": "q", "top_k": 101},
                ):
                    failed = await client.call_tool("search_knowledge_base", args)
                    self.assertTrue(failed.isError)
                    self.assertNotIn("PRIVATE_", str(failed))
                self.assertFalse(
                    (
                        await client.call_tool(
                            "delete_document",
                            {"collection": "stdio", "document_id": "1"},
                        )
                    ).isError
                )
            errors.seek(0)
            self.assertNotIn("PRIVATE_", errors.read())
