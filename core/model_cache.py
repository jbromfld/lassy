"""Warm or verify configured models without connecting to PostgreSQL.

Run with ``python -m core.model_cache``. HF_HOME selects the cache;
HF_HUB_OFFLINE=0 permits downloads/updates, while 1 verifies cached models.
"""

import asyncio

from config import get_settings
from core.providers import ProviderFactory


async def warm_models():
    settings = get_settings()
    factory = ProviderFactory()
    try:
        await factory.get_embedding_provider(settings.embedding_config)
        await factory.get_reranker(
            settings.retrieval_config, settings.embedding_config.device
        )
        print("Configured models cached and validated successfully.")
    finally:
        await factory.close()


if __name__ == "__main__":
    asyncio.run(warm_models())
