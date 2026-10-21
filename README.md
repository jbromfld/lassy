# Lassy

Document ingestion and hybrid retrieval backed by PostgreSQL/pgvector, with local
embeddings and optional reranking. The included VS Code extension adds
`@knowledge` and uses VS Code’s selected chat model to generate answers from
retrieved chunks. The service itself needs no LLM or LLM API key.

## Quickstart

Run commands from the repository root. You need an existing PostgreSQL 15+
database with pgvector available, plus either Bazel 9.2.0 or Python 3.13.16 and uv.
The first API startup downloads the configured embedding model unless cached.

### Configure the database

```bash
cp .env.template .env
# Edit .env: set POSTGRES_HOST/PORT/DB/USER/PASSWORD and keep DEBUG=false.
# Initialize only a new, empty database:
psql -h localhost -p 5432 -U YOUR_USER -d YOUR_DATABASE -v ON_ERROR_STOP=1 -1 -f db/init.sql
```

The initializing role needs permission to enable pgvector. Existing installations
should follow the [migration instructions](docs/documents.md), not reinitialize.
The default model and schema both use 768-dimensional embeddings.

### Start with Bazel

Bazel supplies the pinned Python and Node toolchains and locked dependencies.
Export your configured database/model settings before launching; `.env` is not
packaged in Bazel outputs. If your `.env` uses shell-compatible assignments:

```bash
set -a
source .env
set +a
bazel test //:test
bazel build //:build                  # API executable and extension VSIX
bazel run //:server                  # localhost:8000, unless API_PORT is set
```

### Start without Bazel

```bash
uv sync --locked
uv run --locked uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The native service reads `.env`. Alternatively, run the API with Docker Compose:

```bash
# In .env, use POSTGRES_HOST=host.docker.internal for a host database.
# Set API_PORT=8082 to publish the API at localhost:8082.
mkdir -p .cache/huggingface
docker compose up -d --build lassy
```

Compose defaults to port 8082 only when `API_PORT` is unset; the copied `.env`
template sets it to 8000. See [deployment](docs/setup.md) and
[model-cache operations](docs/model-cache.md) for details.

### Ingest and search

Use your running API’s port (8000 below; 8082 if configured for Compose):

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ingest -H 'Content-Type: application/json' \
  -d '{"title":"Local guide","content":"Lassy retrieves document chunks using PostgreSQL keyword and vector search. It splits source documents into chunks, creates local embeddings, and combines keyword matches with cosine similarity using reciprocal rank fusion. An optional local reranker refines the results. Clients receive ranked text with citations and generate answers using their selected chat model. The service stores documents and embeddings in an external PostgreSQL database."}'
curl http://localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"query":"How does Lassy retrieve documents?","top_k":5}'
```

Interactive API documentation is at `/docs`.

## Rebuild and reinstall the VS Code extension

Install VS Code’s `code` command in your PATH. Choose either build path below;
repeat it after extension edits to replace the installed version.

With Bazel:

```bash
bazel test //extension:direct_test
bazel build //extension:vsix
code --install-extension bazel-bin/extension/kbsearch-extension.vsix --force
```

Without Bazel, use Node.js 20.20.2 and npm:

```bash
npm ci --prefix extension            # First setup or after dependency changes
./extension/build.sh                # Test and package
./extension/install.sh              # Install with --force
```

Run **Developer: Reload Window** in VS Code after installing. Set
`kbsearch.connectionMode` to `direct` and `kbsearch.ragUrl` to your API’s
`http://localhost:8000/query` (or port 8082). Open Chat and ask `@knowledge` a
question about ingested content. The extension does not start the API.

For development without reinstalling, open this repository in VS Code and press
F5 after `npm ci --prefix extension`. See the [extension guide](extension/README.md)
for settings, MCP mode, and remote Extension Hosts.

## Reference

- [Setup and deployment](docs/setup.md): database roles, legacy schema replacement, native and Docker operation.
- [Model-cache operations](docs/model-cache.md): preparation, offline use, updates, purging, and volume migration.
- [API and retrieval](docs/api.md): CLI, query/feedback contracts, configuration, reranking, and compatibility.
- [Shared documents and evaluation](docs/documents.md): additive migration, upserts, collections, reindexing, and retrieval judgments.
- [Development and builds](docs/development.md): tests, runtime versions, dependency locks, Bazel targets, and repository boundaries.

OCI release publishing and GitOps deployment are documented in [image releases](docs/releases.md).
