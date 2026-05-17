from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.repositories.db import Database


@dataclass(frozen=True)
class KindleDelivery:
    id: int
    user_id: int
    book_id: str
    status: str


class KindleDeliveriesRepository:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, user_id: int, book_id: str, status: str = "queued") -> int:
        now = _now()
        async with self.db.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO kindle_deliveries (user_id, book_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, book_id, status, now, now),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def update(
        self,
        delivery_id: int,
        *,
        status: str,
        title: str | None = None,
        format: str | None = None,
        filename: str | None = None,
        file_size_bytes: int | None = None,
        error: str | None = None,
    ) -> None:
        fields = {"status": status, "updated_at": _now()}
        optional_fields = {
            "title": title,
            "format": format,
            "filename": filename,
            "file_size_bytes": file_size_bytes,
            "error": error,
        }
        fields.update({key: value for key, value in optional_fields.items() if value is not None})
        assignments = ", ".join(f"{key} = ?" for key in fields)
        async with self.db.connect() as conn:
            await conn.execute(
                f"UPDATE kindle_deliveries SET {assignments} WHERE id = ?",
                (*fields.values(), delivery_id),
            )
            await conn.commit()

    async def count_recent_for_rate_limit(self, user_id: int) -> int:
        since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        async with self.db.connect() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM kindle_deliveries
                    WHERE user_id = ?
                      AND created_at >= ?
                      AND status IN ('queued', 'failed', 'sent')
                    """,
                    (user_id, since),
                )
            ).fetchone()
        return int(row["count"])


def _now() -> str:
    return datetime.now(UTC).isoformat()
