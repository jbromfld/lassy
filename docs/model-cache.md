# Model-cache operations

[← Quickstart](../README.md)

Run commands from the repository root unless stated otherwise.

## Persistent local model cache

Compose mounts `./.cache/huggingface` on the Docker host at `/app/.cache/huggingface` in the container and sets `HF_HOME` to that container path. Both the PyTorch embedding model and the optional reranker use this cache. It survives container replacement, image rebuilds, and `docker compose down`, and is excluded from Git and the Docker build context. Rebuilding the image does not update cached models. With a remote Docker daemon, the source directory must exist on that daemon's host.

### Mount and prepare

Run from the repository root after configuring `.env`:

```bash
mkdir -p .cache/huggingface
docker compose build lassy
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 lassy python -m core.model_cache
```

The preparation command downloads and validates the configured embedding model and, when `ENABLE_RERANKING=true`, the reranker. It uses the same model loaders as the API without opening a database connection. It still reads the normal `.env` settings, including the required PostgreSQL credential fields.

To store models elsewhere, set an absolute host path in `.env` and create that directory before running the commands above:

```dotenv
MODEL_CACHE_DIR=/absolute/path/to/lassy-model-cache
```

Relative paths are resolved from the Compose file's directory. The mount must be writable by the container's `appuser` (UID/GID 1000). On Linux, if necessary, give that user ownership of the dedicated cache directory:

```bash
sudo chown -R 1000:1000 .cache/huggingface
```

Substitute your chosen directory when using a custom `MODEL_CACHE_DIR`. For `docker run`, the equivalent mount and cache setting are:

```bash
docker run --rm --env-file .env \
  --mount "type=bind,source=$(pwd)/.cache/huggingface,target=/app/.cache/huggingface" \
  -e HF_HOME=/app/.cache/huggingface -e HF_HUB_OFFLINE=0 \
  lassy-lassy python -m core.model_cache
```

Use your image tag if it differs from Compose's default `lassy-lassy`.

### Start using cached models

After preparation succeeds, set this in `.env`:

```dotenv
HF_HUB_OFFLINE=1
```

Then verify the cache and start the API:

```bash
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=1 lassy python -m core.model_cache
docker compose up -d --force-recreate lassy
```

Offline mode skips Hugging Face network checks and fails if required model files are missing. It does not skip model loading or validation inference. Leave `HF_HUB_OFFLINE=0` for automatic first-run downloads. See the [Hugging Face cache environment documentation](https://huggingface.co/docs/huggingface_hub/v0.26.5/package_reference/environment_variables).

### Update cached models

Stop the API before modifying its cache, run preparation with downloads enabled, verify offline loading, then recreate the API:

```bash
docker compose stop lassy
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 lassy python -m core.model_cache
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=1 lassy python -m core.model_cache
docker compose up -d --force-recreate lassy
```

The online command checks the configured model repositories for updates and downloads missing files. Previously cached revisions can remain on disk. Changing the embedding model or using updated embedding weights requires re-ingesting documents; a different output dimension also requires a matching database schema. Updating only the reranker does not require re-embedding documents.

### Purge downloaded models

For the default mount, stop the API and remove only the Hub model cache:

```bash
docker compose stop lassy
rm -rf .cache/huggingface/hub
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 lassy python -m core.model_cache
docker compose up -d --force-recreate lassy
```

For a custom mount, remove `hub` inside your `MODEL_CACHE_DIR` instead. Purging removes model downloads and cached revisions, preserves any Hugging Face token outside `hub`, and leaves PostgreSQL documents/embeddings intact. Download again before starting in offline mode. Purging is not needed for routine updates.

### Migrate the previous Docker volume

Before recreating an existing container that still uses the old `embeddings_cache` volume, copy its downloaded files into the new local directory:

```bash
docker compose stop lassy
mkdir -p .cache/huggingface
docker cp lassy:/app/.cache/huggingface/. ./.cache/huggingface/
```

Use your custom destination if applicable, ensure UID 1000 can write to it, then build the new image, verify the cache offline, and recreate the API as above. The old named volume remains available until explicitly removed; the new Compose configuration no longer mounts it.

### Run without Docker

Use the same local cache with the activated Python environment. Export these variables in the shell; Hugging Face does not read the application's `.env` file itself:

```bash
export HF_HOME="$PWD/.cache/huggingface"
mkdir -p "$HF_HOME"
HF_HUB_OFFLINE=0 python -m core.model_cache
export HF_HUB_OFFLINE=1
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

To refresh, stop the API and repeat preparation with `HF_HUB_OFFLINE=0`. To purge, remove `"$HF_HOME/hub"` while the API is stopped and prepare again. Sharing the cache between native Python and Docker also requires compatible host/container file permissions.
