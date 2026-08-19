from __future__ import annotations

from datetime import UTC, datetime

from app.repositories.db import Database


class AnnouncementsRepository:
    def __init__(self, db: Database):
        self.db = db

    async def was_sent(self, user_id: int, announcement_id: str) -> bool:
        async with self.db.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT 1 FROM user_announcements WHERE user_id=? AND announcement_id=?",
                    (user_id, announcement_id),
                )
            ).fetchone()
        return row is not None

    async def mark_sent(self, user_id: int, announcement_id: str) -> None:
        async with self.db.connect() as connection:
            await connection.execute(
                "INSERT OR IGNORE INTO user_announcements(user_id, announcement_id, sent_at) VALUES(?,?,?)",
                (user_id, announcement_id, datetime.now(UTC).isoformat()),
            )
            await connection.commit()
