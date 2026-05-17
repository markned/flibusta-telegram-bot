from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager

import aiosqlite


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
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_kindle_settings (
                    user_id INTEGER PRIMARY KEY,
                    kindle_email TEXT NOT NULL,
                    preferred_kindle_format TEXT DEFAULT 'epub',
                    send_to_kindle_enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kindle_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    book_id TEXT NOT NULL,
                    title TEXT,
                    format TEXT,
                    filename TEXT,
                    file_size_bytes INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await conn.commit()
