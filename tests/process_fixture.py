"""Test-only real-transport harness with a deterministic synthetic embedder."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

from lassy.config import Settings
from lassy.documents import DocumentRequest, upsert_document
from lassy.mcp import build_server
from lassy.service import Service
from lassy.store import PgVectorStore
from lassy.types import EmbeddingProvider


class SyntheticEmbedder(EmbeddingProvider):
    identity_name = "synthetic"

    def split_text(self, text):
        return [text]

    def chunking_identity(self):
        return {}

    def get_dimension(self):
        return 768

    def get_model_name(self):
        return self.identity_name

    async def embed_text(self, text):
        if text == "private-error":
            raise RuntimeError("PRIVATE_DATABASE_PASSWORD")
        return [1.0] + [0.0] * 767

    async def embed_batch(self, texts):
        if directory := os.environ.get("LASSY_TEST_SIGNALS"):
            directory = Path(directory)
            with (directory / "embeddings").open("a") as output:
                output.write("batch\n")
            if os.environ.get("LASSY_TEST_HOLD") == "1":
                (directory / "started").touch()
                async with asyncio.timeout(15):
                    while not (directory / "release").exists():
                        await asyncio.sleep(0.02)
        return [await self.embed_text(text) for text in texts]

    async def close(self):
        pass


@asynccontextmanager
async def synthetic_service():
    dsn = os.environ["LASSY_TEST_DSN"]
    # Refuse to run against any application database, even if invoked manually.
    from urllib.parse import urlsplit

    if not urlsplit(dsn).path.lstrip("/").startswith("lassy_test_"):
        raise RuntimeError("Refusing non-test database")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        service = Service(
            Settings(postgres_user="test", postgres_password="test"),
            pool,
            SyntheticEmbedder(),
        )
        await service.ready(initialize=True)
        yield service
    finally:
        await pool.close()


async def operation(action):
    async with synthetic_service() as service:
        print("attempt", flush=True)
        if action == "delete":
            result = await service.delete("process", "1")
        else:
            result = await upsert_document(
                PgVectorStore(service.pool),
                service.embedder,
                service.settings.chunking_config,
                "process",
                "1",
                DocumentRequest(content="Synthetic research evidence."),
            )
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["stdio"]:
        build_server(lifespan_factory=synthetic_service).run(transport="stdio")
    else:
        asyncio.run(operation(sys.argv[1]))
