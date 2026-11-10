# Offline model cache

Run `lassy-models prepare` explicitly to download pinned safe model snapshots and
validate dimensions. Set `ENABLE_RERANKING=true` to also prepare/test the pinned
cross-encoder. Preparation needs no database credentials. Configure models through
exported variables or an explicit `LASSY_ENV_FILE` and cache through `HF_HOME`.

Runtime always uses local-only snapshots and disables remote model code and
telemetry. Missing/incomplete snapshots cause startup failure; runtime never
repairs/downloads the cache. Keep the embedding snapshot and tokenizer configuration
that built an index. Changing model identity requires an explicit reindex as
explained in [document upgrades](documents.md).

For native/stdio clients use an absolute `HF_HOME`. Compose mounts
`MODEL_CACHE_DIR` (default `./.cache/huggingface`) at `/app/.cache/huggingface`;
prepare into that same host directory before startup. The cache must be readable
by container UID 1000. Models are not baked into OCI images.

The managed GitOps cache source is configured in the sibling environment
repository. Its `.local/huggingface` preparation directory and an adopted
`~/.cache/huggingface` deployment mount may differ: explicitly map the prepared
cache before deployment. Shared preparation honors enabled Lassy reranking and
also prepares Babar's separate MiniLM snapshot. Purge snapshots only after stopping
all processes that use them and preserving models required for rollback.
