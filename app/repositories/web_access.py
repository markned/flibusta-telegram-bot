from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import secrets

from app.repositories.db import Database


_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True)
class WebSession:
    user_id: int
    expires_at: str


class WebAccessRepository:
    def __init__(self, db: Database, secret: str) -> None:
        if not secret:
            raise ValueError("WEB_AUTH_SECRET is required")
        self.db = db
        self._secret = secret.encode("utf-8")

    async def create_pairing_code(self, user_id: int, ttl_seconds: int = 600) -> str:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=max(60, ttl_seconds))
        for _ in range(5):
            raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
            code_hash = self._hash(f"code:{raw}")
            try:
                async with self.db.connect() as conn:
                    await conn.execute(
                        "DELETE FROM web_pairing_codes WHERE user_id = ? OR expires_at < ?",
                        (user_id, now.isoformat()),
                    )
                    await conn.execute(
                        """
                        INSERT INTO web_pairing_codes (
                            code_hash, user_id, created_at, expires_at, used_at
                        ) VALUES (?, ?, ?, ?, NULL)
                        """,
                        (code_hash, user_id, now.isoformat(), expires_at.isoformat()),
                    )
                    await conn.commit()
                return f"{raw[:4]}-{raw[4:]}"
            except Exception as exc:
                if "unique" not in str(exc).casefold():
                    raise
        raise RuntimeError("Could not create a unique web pairing code")

    async def consume_pairing_code(
        self,
        code: str,
        *,
        session_days: int = 90,
        max_sessions_per_user: int = 5,
    ) -> str | None:
        normalized = _normalize_code(code)
        if len(normalized) != 8:
            return None
        now = datetime.now(UTC)
        code_hash = self._hash(f"code:{normalized}")
        token = secrets.token_urlsafe(32)
        token_hash = self._hash(f"session:{token}")
        expires_at = now + timedelta(days=max(1, session_days))
        async with self.db.connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            row = await (
                await conn.execute(
                    """
                    SELECT user_id FROM web_pairing_codes
                    WHERE code_hash = ? AND used_at IS NULL AND expires_at >= ?
                    """,
                    (code_hash, now.isoformat()),
                )
            ).fetchone()
            if row is None:
                await conn.rollback()
                return None
            user_id = int(row["user_id"])
            await conn.execute(
                "UPDATE web_pairing_codes SET used_at = ? WHERE code_hash = ?",
                (now.isoformat(), code_hash),
            )
            await conn.execute(
                """
                INSERT INTO web_sessions (
                    token_hash, user_id, created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, user_id, now.isoformat(), expires_at.isoformat(), now.isoformat()),
            )
            await conn.execute(
                """
                DELETE FROM web_sessions
                WHERE user_id = ? AND token_hash NOT IN (
                    SELECT token_hash FROM web_sessions
                    WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
                )
                """,
                (user_id, user_id, max(1, max_sessions_per_user)),
            )
            await conn.commit()
        return token

    async def get_session(self, token: str | None) -> WebSession | None:
        if not token:
            return None
        now = datetime.now(UTC)
        token_hash = self._hash(f"session:{token}")
        async with self.db.connect() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT user_id, expires_at FROM web_sessions
                    WHERE token_hash = ? AND expires_at >= ?
                    """,
                    (token_hash, now.isoformat()),
                )
            ).fetchone()
            if row is None:
                return None
            await conn.execute(
                "UPDATE web_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now.isoformat(), token_hash),
            )
            await conn.commit()
        return WebSession(user_id=int(row["user_id"]), expires_at=row["expires_at"])

    async def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        async with self.db.connect() as conn:
            await conn.execute(
                "DELETE FROM web_sessions WHERE token_hash = ?",
                (self._hash(f"session:{token}"),),
            )
            await conn.commit()

    async def prune_expired(self) -> tuple[int, int]:
        now = datetime.now(UTC).isoformat()
        async with self.db.connect() as conn:
            codes = await conn.execute("DELETE FROM web_pairing_codes WHERE expires_at < ?", (now,))
            sessions = await conn.execute("DELETE FROM web_sessions WHERE expires_at < ?", (now,))
            await conn.commit()
        return codes.rowcount, sessions.rowcount

    def _hash(self, value: str) -> str:
        return hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _normalize_code(value: str) -> str:
    return "".join(char for char in value.upper() if char.isalnum())
