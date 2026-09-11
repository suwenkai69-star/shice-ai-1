ALTER TABLE upload_documents ADD COLUMN IF NOT EXISTS storage_policy TEXT NOT NULL DEFAULT 'EPHEMERAL';
ALTER TABLE upload_documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE upload_documents ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE upload_documents ADD COLUMN IF NOT EXISTS processed_at BIGINT;

CREATE TABLE IF NOT EXISTS upload_drafts (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES upload_documents(id),
    draft_type TEXT NOT NULL CHECK(draft_type IN ('FILE','IMAGE')),
    preview_json TEXT NOT NULL,
    canonical_payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
