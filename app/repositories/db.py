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
            yield conn

    async def initialize(self) -> None:
        async with self.connect() as conn:
            await run_migrations(conn)

    async def ping(self) -> bool:
        try:
            async with self.connect() as conn:
                row = await (await conn.execute("SELECT 1")).fetchone()
            return bool(row)
        except Exception:
            return False
