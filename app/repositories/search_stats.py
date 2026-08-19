from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.repositories.db import Database
from app.services.search.planner import build_search_plan


class SearchStatsRepository:
    """Aggregate no-result signals without storing user queries."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record_miss(self, query: str, max_fallback_queries: int = 3) -> None:
        plan = build_search_plan(query, max_fallback_queries=max_fallback_queries)
        length = len(plan.cleaned_query)
        bucket = "short" if length < 5 else "medium" if length < 25 else "long"
        keyboard = int(len(plan.primary_queries) > 1)
        async with self.db.connect() as conn:
            await conn.execute(
                """INSERT INTO search_miss_stats(day, mode, length_bucket, keyboard_variant, fallback_count, count)
                   VALUES(?, ?, ?, ?, ?, 1)
                   ON CONFLICT(day, mode, length_bucket, keyboard_variant, fallback_count)
                   DO UPDATE SET count = count + 1""",
                (datetime.now(UTC).date().isoformat(), plan.mode.value, bucket, keyboard, len(plan.fallback_queries)),
            )
            await conn.commit()

    async def summary(self, days: int = 7) -> tuple[int, list[tuple[str, int]]]:
        since = (datetime.now(UTC).date() - timedelta(days=max(1, days) - 1)).isoformat()
        async with self.db.connect() as conn:
            rows = await (
                await conn.execute(
                    "SELECT mode, SUM(count) AS total FROM search_miss_stats WHERE day >= ? GROUP BY mode ORDER BY total DESC",
                    (since,),
                )
            ).fetchall()
        totals = [(str(row["mode"]), int(row["total"])) for row in rows]
        return sum(value for _, value in totals), totals
