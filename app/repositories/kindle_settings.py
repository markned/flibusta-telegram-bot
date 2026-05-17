from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.repositories.db import Database


@dataclass(frozen=True)
class KindleSettings:
    user_id: int
    kindle_email: str
    preferred_kindle_format: str
    send_to_kindle_enabled: bool


class KindleSettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, user_id: int) -> KindleSettings | None:
        async with self.db.connect() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT user_id, kindle_email, preferred_kindle_format, send_to_kindle_enabled
                    FROM user_kindle_settings
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )
            ).fetchone()
        if row is None:
            return None
        return KindleSettings(
            user_id=row["user_id"],
            kindle_email=row["kindle_email"],
            preferred_kindle_format=row["preferred_kindle_format"],
            send_to_kindle_enabled=bool(row["send_to_kindle_enabled"]),
        )

    async def upsert(self, user_id: int, kindle_email: str, preferred_format: str = "epub") -> KindleSettings:
        now = _now()
        async with self.db.connect() as conn:
            await conn.execute(
                """
                INSERT INTO user_kindle_settings (
                    user_id, kindle_email, preferred_kindle_format, send_to_kindle_enabled, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    kindle_email = excluded.kindle_email,
                    preferred_kindle_format = excluded.preferred_kindle_format,
                    send_to_kindle_enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (user_id, kindle_email, preferred_format, now, now),
            )
            await conn.commit()
        return KindleSettings(user_id, kindle_email, preferred_format, True)

    async def delete(self, user_id: int) -> None:
        async with self.db.connect() as conn:
            await conn.execute("DELETE FROM user_kindle_settings WHERE user_id = ?", (user_id,))
            await conn.commit()

    async def update_preferred_format(self, user_id: int, preferred_format: str) -> KindleSettings | None:
        async with self.db.connect() as conn:
            await conn.execute(
                """
                UPDATE user_kindle_settings
                SET preferred_kindle_format = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (preferred_format, _now(), user_id),
            )
            await conn.commit()
        return await self.get(user_id)


def _now() -> str:
    return datetime.now(UTC).isoformat()
