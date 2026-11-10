-- Apply after 002_documents.sql. Existing vectors stay searchable until backfill.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_revision TEXT;

-- Query history needs IDs/scores for feedback, not retained source excerpts.
UPDATE queries SET response=jsonb_set(response, '{chunks}', COALESCE((
    SELECT jsonb_agg(chunk - 'content')
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(response->'chunks')='array'
        THEN response->'chunks' ELSE '[]'::jsonb END) AS chunk), '[]'::jsonb))
WHERE jsonb_typeof(response->'chunks')='array'
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(response->'chunks')='array'
        THEN response->'chunks' ELSE '[]'::jsonb END) AS chunk WHERE chunk ? 'content');
