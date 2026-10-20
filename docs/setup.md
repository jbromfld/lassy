# Setup and deployment

[← Quickstart](../README.md)

Run commands from the repository root unless stated otherwise.

## Setup

Use Python 3.13.16 and PostgreSQL 15+ with pgvector installed on the server. The default schema and embedding settings use 768-dimensional `sentence-transformers/all-mpnet-base-v2` embeddings. Model files must be downloaded on first use or already cached.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
```

Set `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in `.env`. Native API: use `localhost`. API container connecting to the host: use `host.docker.internal`. Set `DEBUG=false`; environment variables override `.env`, so unset any unrelated nonboolean shell `DEBUG` variable.

Have your administrator create the database and grant the initializing role permission to enable pgvector. Initialize once in an empty database:

```bash
psql -h localhost -p 5432 -U YOUR_USER -d YOUR_DATABASE -v ON_ERROR_STOP=1 -1 -f db/init.sql
```

The schema contains documents, embeddings, queries, metrics, and feedback. It creates no database or role. If the runtime role differs from the owner, grant it schema usage and access to tables, sequences, and functions.

### Replace an older service schema

This version deliberately removes server-side generation and its schema. To replace an older schema, **delete the old service data and recreate it**, then re-ingest your documents:

```bash
psql -h localhost -p 5432 -U YOUR_USER -d YOUR_DATABASE -v ON_ERROR_STOP=1 -1 \
  -f db/cleanup.sql -f db/init.sql
```

Startup never resets or migrates a database automatically. There are no database configuration profiles or seed records. A different embedding dimension requires a matching schema and environment settings, followed by re-ingestion.

## Run

From the repository root:

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8000
# Alternatively, reads API_PORT and DEBUG; binds to 0.0.0.0:
python -m api.main
```

Or, after configuring `.env` and the external database:

```bash
mkdir -p .cache/huggingface
docker compose up -d --build lassy
docker compose logs -f lassy
docker compose down
```

The Docker build context includes only runtime source and dependency/build files; local virtual environments, `.env`, Git history, tests and downloaded models are excluded. The Dockerfile installs binary wheels directly with a BuildKit uv download cache, skips bytecode compilation, and installs no compiler toolchain. Application-only edits reuse the dependency layer. Builds require compatible wheels for the target Python/Linux architecture and fail instead of compiling packages from source.

Compose runs only the API. `API_PORT` controls its published host port (8082 when unset); the container listens on 8000. Model downloads persist in a local bind mount described in the model-cache guide. The API has no authentication; bind/publish it to your intended network.

See [model-cache operations](model-cache.md) for download, offline, and cache-maintenance instructions.
