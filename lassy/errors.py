"""Actionable startup diagnostics whose messages never contain external exception text."""

STARTUP_MESSAGES = {
    "missing_document_schema": "Missing document/index tables. Initialize a fresh database or apply the documented additive migrations; see docs/documents.md.",
    "missing_identity_schema": "Missing lassy_index_identity. Apply db/004_mcp.sql, then verify and adopt the existing index identity; see docs/documents.md.",
    "missing_embedding_revision": "Missing documents.embedding_revision. Apply db/003_practical.sql before starting Lassy.",
    "dimension_mismatch": "Embedding dimension differs from the index. Use the matching configuration or explicitly migrate and reindex.",
    "identity_unverified": "Existing index identity is unverified. Verify the model snapshot, then run lassy-models adopt-index; use --legacy-unpinned only when the original revision was omitted. See docs/documents.md.",
    "identity_mismatch": "Model or chunking identity differs from this index. Use the original configuration or a separate database and explicitly reindex.",
    "model_missing": "Pinned model is missing or incomplete. Run lassy-models prepare with the runtime configuration and cache.",
    "database_permissions": "Runtime database permissions are insufficient. Check grants on documents, embeddings, and lassy_index_identity.",
}
DEFAULT_STARTUP_MESSAGE = "Check explicit configuration, database access, index migrations, and prepared models; see docs/documents.md."


class StartupError(RuntimeError):
    """A fixed operator-facing message selected by code, never raw exception text."""

    def __init__(self, code):
        super().__init__(STARTUP_MESSAGES[code])
        self.code = code


def startup_message(error):
    # MCP lifespan failures can be wrapped in task-group exceptions.
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, StartupError):
            return STARTUP_MESSAGES[current.code]
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return DEFAULT_STARTUP_MESSAGE
