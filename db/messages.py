"""Message storage and edit-history preservation."""

from __future__ import annotations

import logging
from datetime import datetime

from db.pool import DatabasePool, get_pool

logger = logging.getLogger(__name__)

# on_message / slash inserts. content stays the original text.
# If an edit arrived before the insert (edited_content still NULL and text differs),
# move that earlier text to edited_content and restore the original into content.
INSERT_MESSAGE_SQL = """
INSERT INTO messages (id, member_id, channel_id, content, created_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    edited_content = messages.content,
    edited_at = CURRENT_TIMESTAMP,
    content = EXCLUDED.content
WHERE messages.edited_content IS NULL
  AND messages.content IS DISTINCT FROM EXCLUDED.content
"""

# Cached edit: pre-edit text is known. Never overwrite messages.content.
INSERT_KNOWN_EDIT_SQL = """
INSERT INTO messages (
    id, member_id, channel_id, content, created_at, edited_content, edited_at
) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
ON CONFLICT (id) DO UPDATE SET
    edited_content = EXCLUDED.edited_content,
    edited_at = CURRENT_TIMESTAMP
WHERE EXCLUDED.edited_content IS DISTINCT FROM COALESCE(messages.edited_content, messages.content)
"""

# Uncached edit: pre-edit text is unknown. Missing row stores the only text we have
# without marking it edited; existing row updates only edited_content / edited_at.
UPSERT_UNSEEN_EDIT_SQL = """
INSERT INTO messages (id, member_id, channel_id, content, created_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    edited_content = %s,
    edited_at = CURRENT_TIMESTAMP
WHERE %s IS DISTINCT FROM COALESCE(messages.edited_content, messages.content)
"""

UPDATE_EDITED_CONTENT_SQL = """
UPDATE messages
SET edited_content = %s,
    edited_at = CURRENT_TIMESTAMP
WHERE id = %s
  AND %s IS DISTINCT FROM COALESCE(edited_content, content)
"""


class MessagesRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def insert_message(
        self,
        message_id: int,
        member_id: int,
        channel_id: int,
        content: str,
        created_at: datetime,
    ) -> None:
        self.pool.execute(
            INSERT_MESSAGE_SQL,
            (message_id, member_id, channel_id, content, created_at),
        )

    def record_edit(
        self,
        message_id: int,
        new_content: str | None,
        *,
        member_id: int | None = None,
        channel_id: int | None = None,
        created_at: datetime | None = None,
        original_content: str | None = None,
    ) -> str:
        """Store a content edit without overwriting ``messages.content``.

        Returns ``unchanged``, ``stored``, ``updated``, or ``missing``.
        """
        new_text = "" if new_content is None else str(new_content)
        known_original = None if original_content is None else str(original_content)
        if known_original is not None and known_original == new_text:
            return "unchanged"

        can_insert = (
            member_id is not None and channel_id is not None and created_at is not None
        )
        with self.pool.transaction() as cursor:
            if known_original is not None and can_insert:
                cursor.execute(
                    INSERT_KNOWN_EDIT_SQL,
                    (
                        message_id,
                        member_id,
                        channel_id,
                        known_original,
                        created_at,
                        new_text,
                    ),
                )
                return "stored" if cursor.rowcount else "unchanged"
            if known_original is None and can_insert:
                cursor.execute(
                    UPSERT_UNSEEN_EDIT_SQL,
                    (
                        message_id,
                        member_id,
                        channel_id,
                        new_text,
                        created_at,
                        new_text,
                        new_text,
                    ),
                )
                return "stored" if cursor.rowcount else "unchanged"
            cursor.execute(
                UPDATE_EDITED_CONTENT_SQL,
                (new_text, message_id, new_text),
            )
            if cursor.rowcount:
                return "updated"

        logger.debug(
            "Edit for message %s was not stored; row missing and author unknown",
            message_id,
        )
        return "missing"
