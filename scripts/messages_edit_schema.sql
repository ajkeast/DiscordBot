-- Original Discord message text stays in messages.content.
-- edited_content is the latest text after a real content edit (NULL if never edited).
-- edited_at is when edited_content was last written.
--
-- Additive and safe to re-run on a live database: nullable columns only.
-- No data is rewritten. Originals already overwritten in messages.content
-- cannot be recovered. edited_content stays NULL until a later edit.
--
-- Applied by DataOperations.ensure_message_edit_columns() at startup and
-- before message writes.
-- Deploy: docker compose -f docker-compose.prod.yml up --build -d

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS edited_content VARCHAR(4000);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP;

COMMENT ON COLUMN messages.edited_content IS
    'Latest message text after a real content edit. NULL if content was never edited. Embed-only updates are ignored.';

COMMENT ON COLUMN messages.edited_at IS
    'When edited_content was last set. NULL if content was never edited.';
