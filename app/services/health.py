from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from app.repositories.db import Database


@dataclass(frozen=True)
class HealthSnapshot:
    sqlite_ok: bool
    database_bytes: int
    wal_bytes: int
    disk_free_bytes: int


async def collect_health(db: Database) -> HealthSnapshot:
    path = Path(db.path)
    disk_root = path.resolve().parent
    return HealthSnapshot(
        sqlite_ok=await db.ping(),
        database_bytes=path.stat().st_size if path.exists() else 0,
        wal_bytes=Path(f"{path}-wal").stat().st_size if Path(f"{path}-wal").exists() else 0,
        disk_free_bytes=shutil.disk_usage(disk_root).free,
    )


def human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if amount < 1024 or unit == "ТБ":
            return f"{amount:.0f} {unit}" if unit == "Б" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} ТБ"
