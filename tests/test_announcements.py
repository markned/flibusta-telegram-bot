from __future__ import annotations

import asyncio
from pathlib import Path

from app.repositories.announcements import AnnouncementsRepository
from app.repositories.db import Database


def test_announcement_marking_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(str(tmp_path / "bot.sqlite"))
        await db.initialize()
        repo = AnnouncementsRepository(db)
        assert not await repo.was_sent(42, "brand")
        await repo.mark_sent(42, "brand")
        await repo.mark_sent(42, "brand")
        assert await repo.was_sent(42, "brand")
        async with db.connect() as connection:
            count = int(
                (
                    await (
                        await connection.execute(
                            "SELECT COUNT(*) FROM user_announcements WHERE user_id=42"
                        )
                    ).fetchone()
                )[0]
            )
        assert count == 1

    asyncio.run(scenario())
