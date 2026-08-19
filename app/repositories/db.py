from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager

import aiosqlite
from app.repositories.migrations import run_migrations


class Database:
    def __init__(self, path: str):
        self.path = path

    @asynccontextmanager
    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA busy_timeout=5000")
            # WAL + NORMAL keeps writes fast without sacrificing database
            # consistency.  A small page cache helps the hot SQLite indexes
            # while staying friendly to the production 1 GB VPS.
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA temp_store=MEMORY")
            await conn.execute("PRAGMA cache_size=-2048")
            await conn.execute("PRAGMA wal_autocheckpoint=500")
            yield conn

    async def initialize(self) -> None:
        async with self.connect() as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA journal_size_limit=8388608")
            await run_migrations(conn)
            await conn.execute("PRAGMA optimize")

    async def ping(self) -> bool:
        try:
            async with self.connect() as conn:
                row = await (await conn.execute("SELECT 1")).fetchone()
            return bool(row)
        except Exception:
            return False
