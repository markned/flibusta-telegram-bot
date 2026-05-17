from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.repositories.db import Database


@dataclass(frozen=True)
class KindleDelivery:
    id: int
    user_id: int
    book_id: str
    title: str | None
    format: str | None
    filename: str | None
    file_size_bytes: int | None
    status: str
    error: str | None
    created_at: str
    updated_at: str
    retry_of_delivery_id: int | None = None
    attempts: int = 0
    last_error: str | None = None
    last_attempt_at: str | None = None


class KindleDeliveriesRepository:
    def __init__(self, db: Database):
        self.db = db

    async def create_delivery(self, user_id: int, book_id: str, status: str = "queued", retry_of_delivery_id: int | None = None) -> int:
        now = _now()
        async with self.db.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO kindle_deliveries (user_id, book_id, status, created_at, updated_at, retry_of_delivery_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, book_id, status, now, now, retry_of_delivery_id),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def update_status(
        self,
        delivery_id: int,
        status: str,
        *,
        title: str | None = None,
        format: str | None = None,
        filename: str | None = None,
        file_size_bytes: int | None = None,
        error: str | None = None,
        last_error: str | None = None,
    ) -> None:
        fields = {"status": status, "updated_at": _now()}
        optional_fields = {
            "title": title,
            "format": format,
            "filename": filename,
            "file_size_bytes": file_size_bytes,
            "error": error,
            "last_error": last_error,
        }
        fields.update({key: value for key, value in optional_fields.items() if value is not None})
        assignments = ", ".join(f"{key} = ?" for key in fields)
        async with self.db.connect() as conn:
            await conn.execute(
                f"UPDATE kindle_deliveries SET {assignments} WHERE id = ?",
                (*fields.values(), delivery_id),
            )
            await conn.commit()

    async def mark_failed(self, delivery_id: int, error: str) -> None:
        await self.update_status(delivery_id, "failed", error=error, last_error=error)
    async def increment_attempt(self, delivery_id:int)->None:
        async with self.db.connect() as c:
            await c.execute("UPDATE kindle_deliveries SET attempts=attempts+1,last_attempt_at=?,updated_at=? WHERE id=?",(_now(),_now(),delivery_id)); await c.commit()
    async def get_by_id(self, delivery_id:int):
        async with self.db.connect() as c: row=await (await c.execute("SELECT * FROM kindle_deliveries WHERE id=?",(delivery_id,))).fetchone()
        return None if row is None else _delivery_from_row(row)
    async def get_latest_failed_for_user(self,user_id:int):
        async with self.db.connect() as c: row=await (await c.execute("SELECT * FROM kindle_deliveries WHERE user_id=? AND status='failed' ORDER BY created_at DESC,id DESC LIMIT 1",(user_id,))).fetchone()
        return None if row is None else _delivery_from_row(row)
    async def get_recent_failures(self,limit=10):
        async with self.db.connect() as c: rows=await (await c.execute("SELECT * FROM kindle_deliveries WHERE status='failed' ORDER BY created_at DESC LIMIT ?",(limit,))).fetchall()
        return [_delivery_from_row(r) for r in rows]
    async def cleanup_completed(self,days:int):
        since=(datetime.now(UTC)-timedelta(days=days)).isoformat()
        async with self.db.connect() as c:
            cur=await c.execute("DELETE FROM kindle_deliveries WHERE status IN ('sent','failed') AND created_at < ?",(since,)); await c.commit(); return cur.rowcount

    async def get_recent_for_user(self, user_id: int, limit: int = 10) -> list[KindleDelivery]:
        async with self.db.connect() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT *
                    FROM kindle_deliveries
                    WHERE user_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )
            ).fetchall()
        return [_delivery_from_row(row) for row in rows]

    async def count_recent_for_user(self, user_id: int, hours: int = 1) -> int:
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        async with self.db.connect() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM kindle_deliveries
                    WHERE user_id = ?
                      AND created_at >= ?
                      AND status IN ('queued', 'downloading', 'downloaded', 'converting', 'sending', 'sent', 'failed')
                    """,
                    (user_id, since),
                )
            ).fetchone()
        return int(row["count"])

    async def count_recent_failures(self, hours: int = 24) -> int:
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        async with self.db.connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT COUNT(*) AS count FROM kindle_deliveries WHERE status = 'failed' AND created_at >= ?",
                    (since,),
                )
            ).fetchone()
        return int(row["count"])

    async def mark_interrupted_inflight_failed(self) -> int:
        now = _now()
        async with self.db.connect() as conn:
            cursor = await conn.execute(
                """
                UPDATE kindle_deliveries
                SET status = 'failed',
                    error = 'interrupted by process restart',
                    updated_at = ?
                WHERE status IN ('queued', 'downloading', 'downloaded', 'converting', 'sending')
                """,
                (now,),
            )
            await conn.commit()
            return cursor.rowcount

    # Backward-compatible aliases for first-iteration callers/tests.
    async def create(self, user_id: int, book_id: str, status: str = "queued") -> int:
        return await self.create_delivery(user_id, book_id, status)

    async def update(self, delivery_id: int, *, status: str, **kwargs) -> None:
        await self.update_status(delivery_id, status, **kwargs)

    async def count_recent_for_rate_limit(self, user_id: int) -> int:
        return await self.count_recent_for_user(user_id, hours=1)


def _delivery_from_row(row) -> KindleDelivery:
    return KindleDelivery(**dict(row))


def _now() -> str:
    return datetime.now(UTC).isoformat()
