# Runtime and deployment

Lassy needs PostgreSQL 15+ with pgvector. Administrators initialize fresh databases
or apply [additive upgrades and index adoption](documents.md). Runtime credentials
need SELECT/INSERT/UPDATE/DELETE on its three document/index tables, not extension,
database, role, or arbitrary SQL administration. Lassy does not provision roles.

Install CPython 3.13.16 with uv. `uv sync --locked` installs native MCP;
`--extra api` adds HTTP; `--extra dev` adds verification tools. Settings retain
`POSTGRES_HOST/PORT/DB/USER/PASSWORD`, model/chunking/retrieval variables, and
`DB_POOL_SIZE`. `LASSY_ENV_FILE` explicitly selects a native configuration file.
No `.env` is automatically discovered; Compose injects its environment as before.

Use an absolute interpreter with `-m lassy.mcp` in MCP hosts. The process has no
HTTP listener. Use `lassy-api` for optional native HTTP, defaulting to loopback
port 8000, or the README's example environment for port 8082. Containers run
`uvicorn lassy.api:app` on port 8000. Models are prepared separately and loaded
offline. [Cache operations](model-cache.md) describe native/container mounts.

Compose retains the `lassy` service and host port `${API_PORT:-8082}`. Its API
image runs as an unprivileged user; health checks use `/health`. Existing Argo
workloads retain their service addresses, port 8000, single replica, secrets,
model-cache mount, and digest-based release flow. Shared provisioning and GitOps
commands now reference the installed package; there is no Bazel or Node build.

The HTTP interface is for trusted local consumers and has no application auth.
Bind native HTTP to loopback and keep container networking restricted. Runtime
profiles/collection filters are not a multi-user security boundary. Each MCP/API
process has its own model admission queue and model memory footprint, while
PostgreSQL locks coordinate document mutation across all processes.

See [image releases](releases.md) for source-commit publishing and promotion.
These source/configuration changes do not mutate live databases, publish images,
change deployed digests, stop existing adapter processes, or install/uninstall
VS Code extensions. Remove obsolete host entries/extensions during local adoption.
