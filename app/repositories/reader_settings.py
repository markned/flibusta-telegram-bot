from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.repositories.db import Database


SUPPORTED_READER_PROVIDERS = {"pocketbook"}


@dataclass(frozen=True)
class ReaderSettings:
    user_id: int
    provider: str
    destination_email: str
    preferred_format: str
    enabled: bool
    sender_confirmed: bool


class ReaderSettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, user_id: int, provider: str) -> ReaderSettings | None:
        _validate_provider(provider)
        async with self.db.connect() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT user_id, provider, destination_email, preferred_format,
                           enabled, sender_confirmed
                    FROM user_reader_settings
                    WHERE user_id = ? AND provider = ?
                    """,
                    (user_id, provider),
                )
            ).fetchone()
        return _from_row(row) if row is not None else None

    async def upsert(
        self,
        user_id: int,
        provider: str,
        destination_email: str,
        *,
        preferred_format: str = "epub",
    ) -> ReaderSettings:
        _validate_provider(provider)
        now = _now()
        async with self.db.connect() as conn:
            await conn.execute(
                """
                INSERT INTO user_reader_settings (
                    user_id, provider, destination_email, preferred_format,
                    enabled, sender_confirmed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 0, ?, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    destination_email = excluded.destination_email,
                    preferred_format = excluded.preferred_format,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, destination_email, preferred_format, now, now),
            )
            await conn.commit()
        settings = await self.get(user_id, provider)
        assert settings is not None
        return settings

    async def update_format(self, user_id: int, provider: str, preferred_format: str) -> ReaderSettings | None:
        _validate_provider(provider)
        async with self.db.connect() as conn:
            await conn.execute(
                """
                UPDATE user_reader_settings
                SET preferred_format = ?, updated_at = ?
                WHERE user_id = ? AND provider = ?
                """,
                (preferred_format, _now(), user_id, provider),
            )
            await conn.commit()
        return await self.get(user_id, provider)

    async def set_sender_confirmed(self, user_id: int, provider: str, confirmed: bool = True) -> ReaderSettings | None:
        _validate_provider(provider)
        async with self.db.connect() as conn:
            await conn.execute(
                """
                UPDATE user_reader_settings
                SET sender_confirmed = ?, updated_at = ?
                WHERE user_id = ? AND provider = ?
                """,
                (1 if confirmed else 0, _now(), user_id, provider),
            )
            await conn.commit()
        return await self.get(user_id, provider)

    async def delete(self, user_id: int, provider: str) -> None:
        _validate_provider(provider)
        async with self.db.connect() as conn:
            await conn.execute(
                "DELETE FROM user_reader_settings WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            )
            await conn.commit()


def _validate_provider(provider: str) -> None:
    if provider not in SUPPORTED_READER_PROVIDERS:
        raise ValueError(f"Unsupported reader provider: {provider}")


def _from_row(row) -> ReaderSettings:
    return ReaderSettings(
        user_id=row["user_id"],
        provider=row["provider"],
        destination_email=row["destination_email"],
        preferred_format=row["preferred_format"],
        enabled=bool(row["enabled"]),
        sender_confirmed=bool(row["sender_confirmed"]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
