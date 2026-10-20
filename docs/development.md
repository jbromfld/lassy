# Development and build reference

[← Quickstart](../README.md)

Run commands from the repository root unless stated otherwise.

## VS Code and MCP clients

Run `npm ci` and `npm test` in `extension/`, then open the Lassy repository in VS Code and press F5 to launch its Extension Development Host. Default settings are:

```json
{
  "kbsearch.connectionMode": "direct",
  "kbsearch.ragUrl": "http://localhost:8000/query",
  "kbsearch.includeSources": true
}
```

Ask `@knowledge` a question. The extension supplies retrieved chunks to VS Code's selected chat model, then appends sources and query details itself. Model failure falls back to retrieved context. The extension does not start the service. In a remote Extension Host, `localhost` refers to that host.

The API speaks HTTP. MCP mode continues to use the separate `local-mcp-server` adapter; its retrieval calls remain compatible. Other clients can use the same `/query` contract and send chunks to whichever generator they choose.

## Tests

```bash
python -m unittest discover -s tests -v
# Creates/drops uniquely named disposable databases; requires CREATEDB/extension privileges:
RAG_TEST_POSTGRES=1 POSTGRES_HOST=127.0.0.1 DEBUG=false python -m unittest discover -s tests -v
# Also verify live API + CLI ingestion/search/feedback with a cached embedding model:
RAG_TEST_POSTGRES=1 RAG_TEST_MODEL=1 POSTGRES_HOST=127.0.0.1 DEBUG=false python -m unittest discover -s tests -v
```

Run `npm test` in `extension/` for compilation and direct-mode tests. Complete an F5 `@knowledge` check to verify the installed VS Code/chat model environment.

## Standardized environment

Use CPython **3.13.16** and `uv sync --locked` for this repository. The dependency
source of truth is `pyproject.toml` plus committed `uv.lock`. Use
`uv sync --locked --extra dev` when development extras are needed (where defined).
Shared Ansible provisioning and runtime version checks are documented in [local-agent-environment](../../local-agent-environment/README.md).
Its `.tools/bin/uv` provides the pinned uv version independently of PATH.

`requirements.txt` is a generated compatibility export; regenerate through the
shared environment's `scripts/envctl.py export`. `LOCAL_EMBEDDING_REVISION` and
`RERANK_REVISION` optionally pin Hugging Face commits. Prepare the selected revision
before enabling `HF_HUB_OFFLINE=1`. Changing the embedding revision changes index
identity and requires a controlled full reindex, just like changing model weights.

## Product repository and Bazel

The retrieval service and VS Code client share this repository and one root
`MODULE.bazel`. The extension lives in [extension/](../extension/README.md), retains
its `local.kbsearch-extension` identity and configurable service URL, and does not
start the service. Its original Git history was imported with a merge; see
[extension history](../extension/HISTORY.md). The old sibling checkout is a backup.

Use Bazel 9.2.0 from the repository root:

```bash
bazel test //:test
bazel build //:build
# Or build just the required artifact:
bazel build //:server
bazel build //extension:vsix
```

`//:service_test` runs the Python suite without a live database or downloaded model;
`//extension:direct_test` runs the client suite. Both consume the same retrieval
response fixture under `contracts/`. Database/model integration tests remain the
explicit native commands above. The Python executable is `bazel-bin/server`, and
the VSIX is `bazel-bin/extension/kbsearch-extension.vsix`. Export runtime
settings before running `bazel run //:server`; local `.env` files are not
packaged into Bazel outputs.

Bazel declares CPython 3.13.16 and Node 20.20.2 toolchains. Python dependencies
come from the committed uv lock's requirements export; extension dependencies
use `extension/package-lock.json` and its generated pnpm lock. The service and
VSIX can be released independently. Configuration management in the sibling
`local-agent-environment` repository is not required to build this product.

For native client development, use `./extension/build.sh` to test/package and
`./extension/install.sh` to explicitly install. Root VS Code launch/tasks files
use `extension/`; opening the extension directory alone also supports F5.
