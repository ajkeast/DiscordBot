-- Manual migration. The bot does not apply this file.
-- Run it against Postgres before deploying a bot build that writes these columns.
--
-- content stays the original Discord message text.
-- edited_content is the latest text after a real content edit (NULL if never edited).
-- edited_at is when edited_content was last written.
--
-- Additive and safe to re-run: nullable columns only. No data is rewritten.
-- Originals already overwritten in messages.content cannot be recovered.
-- edited_content stays NULL on those rows until a later edit.

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS edited_content VARCHAR(4000);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP;

COMMENT ON COLUMN messages.edited_content IS
    'Latest message text after a real content edit. NULL if content was never edited. Embed-only updates are ignored.';

COMMENT ON COLUMN messages.edited_at IS
    'When edited_content was last set. NULL if content was never edited.';
