"""Explicit model preparation; runtime never downloads model files."""

import argparse
import asyncio
import logging
import os
import sys
from contextlib import redirect_stdout

from .errors import StartupError, startup_message

MODEL_IGNORE_PATTERNS = [
    "*.bin",
    "*.pt",
    "*.pth",
    "*.pkl",
    "*.h5",
    "*.msgpack",
    "*.onnx",
    "*.ot",
    "*.py",
    "openvino/*",
]


def configure_offline():
    # Set before importing HF/Transformers: these packages cache environment flags.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def offline_snapshot(model, revision):
    if not revision:
        raise ValueError("A pinned model revision is required")
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(
            model,
            revision=revision,
            local_files_only=True,
            ignore_patterns=MODEL_IGNORE_PATTERNS,
        )
    except Exception:
        raise StartupError("model_missing") from None


async def load_models(settings):
    from .embedding import LocalEmbeddingProvider
    from .reranking import LocalReranker

    embedder = None
    try:
        # Some third-party loaders write status messages to stdout.
        with redirect_stdout(sys.stderr):
            embedder = await asyncio.to_thread(
                LocalEmbeddingProvider, settings.embedding_config
            )
            reranker = None
            if settings.enable_reranking:
                config = settings.retrieval_config
                reranker = await asyncio.to_thread(
                    LocalReranker,
                    config.rerank_model,
                    settings.local_embedding_device,
                    config.rerank_revision,
                    config.queue_timeout_seconds,
                    config.max_pending,
                )
        return embedder, reranker
    except BaseException:
        if embedder is not None:
            await embedder.close()
        raise


async def prepare(settings):
    from huggingface_hub import snapshot_download

    identities = [(settings.local_embedding_model, settings.local_embedding_revision)]
    if settings.enable_reranking:
        identities.append((settings.rerank_model, settings.rerank_revision))
    # Fetch only safe model weights/tokenizer/configuration, never remote code/pickle.
    for model, revision in identities:
        await asyncio.to_thread(
            snapshot_download,
            model,
            revision=revision,
            ignore_patterns=MODEL_IGNORE_PATTERNS,
        )
    configure_offline()
    embedder, reranker = await load_models(settings)
    try:
        if reranker:
            await reranker.score("Model preparation", ["Local evidence."])
    finally:
        await embedder.close()
        if reranker:
            await reranker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "adopt-index"])
    parser.add_argument(
        "--legacy-unpinned",
        action="store_true",
        help="Adopt verified legacy vectors whose original configuration omitted the model revision",
    )
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        from .config import load_settings

        if args.command == "prepare":
            os.environ["HF_HUB_OFFLINE"] = "0"
            os.environ["TRANSFORMERS_OFFLINE"] = "0"
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            with redirect_stdout(sys.stderr):
                asyncio.run(prepare(load_settings(model_only=True)))
            print("Pinned local models prepared and verified.", file=sys.stderr)
        else:
            configure_offline()
            from .service import adopt_index

            asyncio.run(
                adopt_index(load_settings(), legacy_unpinned=args.legacy_unpinned)
            )
            print(
                "Verified index identity adopted. No vectors were changed.",
                file=sys.stderr,
            )
    except Exception as exc:
        raise SystemExit(f"Model operation failed: {startup_message(exc)}") from None


if __name__ == "__main__":
    main()
