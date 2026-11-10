# Development and verification

Use CPython 3.13.16 and `uv sync --locked --extra api --extra dev`. The package is
`lassy`; native MCP, HTTP, and model maintenance use installed entry points.
There is no Bazel, Node/extension toolchain, generated requirements export, or
standalone retrieval adapter. SQL migrations remain explicit administrator actions.

```sh
uv run --no-sync ruff check lassy tests
uv run --no-sync ruff format --check lassy tests
uv run --no-sync pytest -q
LASSY_TEST_POSTGRES=1 uv run --no-sync pytest -q
```

The default tests need no running database or model downloads. Integration tests
create/drop unique `lassy_test_*` databases with synthetic models and content,
including real stdio MCP sessions and independent OS-process mutation races.
`LASSY_TEST_ADMIN_DSN` defaults to `postgresql:///postgres`; its role needs CREATEDB
and extension privileges. Tests never reset application databases. Live model/cache
verification uses explicit `lassy-models prepare`, separate from synthetic tests.

Dependency constraints live in `pyproject.toml`; exact supported macOS ARM64 and
Linux ARM64 CPU resolutions live in `uv.lock`. Linux uses the CPU PyTorch index.
Build the API with `docker build -t lassy-test .`; native MCP needs no image.

## Full-lock security audit

Audit every locked third-party version, including optional/development and both
platform branches. Strip the PyTorch `+cpu` local version suffix only for advisory
lookup; the installed CPU wheel remains pinned. This avoids hiding optional or
foreign-platform packages behind the current interpreter's environment markers:

```sh
uv run --no-sync python scripts/audit_lock.py
```

The script invokes pip-audit against all resolved versions without ignored
advisories. [Security reconciliation](security.md) records the pre-refactor baseline
and replacement package versions. GitHub rescans the default branch after changes
are merged; deleting duplicate manifests alone does not establish remediation.
