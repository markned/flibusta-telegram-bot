from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from app.repositories.db import Database


def _now() -> datetime:
    return datetime.now(UTC)


class CacheRepository:
    def __init__(self, db: Database): self.db = db

    async def get(self, key: str) -> Any | None:
        async with self.db.connect() as c:
            row = await (await c.execute('SELECT payload_json, expires_at FROM flibusta_cache WHERE cache_key=?', (key,))).fetchone()
        if row is None or datetime.fromisoformat(row['expires_at']) <= _now(): return None
        try: return json.loads(row['payload_json'])
        except json.JSONDecodeError: return None

    async def set(self, key: str, cache_type: str, payload: Any, ttl_seconds: int) -> None:
        created = _now(); expires = created + timedelta(seconds=ttl_seconds)
        encoded = json.dumps(_jsonable(payload), ensure_ascii=False)
        async with self.db.connect() as c:
            await c.execute('''INSERT INTO flibusta_cache(cache_key,cache_type,payload_json,created_at,expires_at)
             VALUES(?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, created_at=excluded.created_at, expires_at=excluded.expires_at''',
             (key, cache_type, encoded, created.isoformat(), expires.isoformat()))
            await c.commit()

    async def stats(self) -> tuple[int, dict[str, int], int]:
        now = _now().isoformat()
        async with self.db.connect() as c:
            total = await (await c.execute('SELECT COUNT(*) FROM flibusta_cache')).fetchone()
            rows = await (await c.execute('SELECT cache_type,COUNT(*) AS count FROM flibusta_cache GROUP BY cache_type')).fetchall()
            expired = await (await c.execute('SELECT COUNT(*) FROM flibusta_cache WHERE expires_at <= ?', (now,))).fetchone()
        return int(total[0]), {r['cache_type']: int(r['count']) for r in rows}, int(expired[0])

    async def clear(self, *, all_rows: bool = False) -> int:
        async with self.db.connect() as c:
            cur = await c.execute('DELETE FROM flibusta_cache' if all_rows else 'DELETE FROM flibusta_cache WHERE expires_at <= ?', (() if all_rows else (_now().isoformat(),)))
            await c.commit(); return cur.rowcount


def _jsonable(value: Any) -> Any:
    if is_dataclass(value): return asdict(value)
    if isinstance(value, tuple): return [_jsonable(v) for v in value]
    if isinstance(value, list): return [_jsonable(v) for v in value]
    if isinstance(value, dict): return {k: _jsonable(v) for k, v in value.items()}
    return value
