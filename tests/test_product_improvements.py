import asyncio

from app.flibusta import parse_book_details, parse_series_page
from app.repositories.db import Database
from app.repositories.favorites import FavoritesRepository
from app.repositories.search_stats import SearchStatsRepository
from app.repositories.web_access import WebAccessRepository
from app.services.search.planner import build_search_plan


def run(coro):
    return asyncio.run(coro)


def test_parses_series_link_and_series_page() -> None:
    details = parse_book_details(
        '<div id="main"><h1>Дюна - Фрэнк Герберт</h1><p>Серия: <a href="/sequence/42">Хроники Дюны</a> #2</p></div>',
        "https://flibusta.test",
        "10",
        "https://flibusta.test/b/10",
    )
    assert details.series[0].series_id == "42"
    assert details.series[0].position == "2"
    name, books = parse_series_page(
        '<div id="main"><h1>Серия: Хроники Дюны</h1><a href="/b/10">Дюна</a></div>', "42"
    )
    assert name == "Хроники Дюны"
    assert books[0].book_id == "10"


def test_web_sessions_can_be_listed_and_revoked(tmp_path) -> None:
    async def scenario():
        db = Database(str(tmp_path / "bot.db")); await db.initialize()
        repo = WebAccessRepository(db, "secret")
        code = await repo.create_pairing_code(7)
        assert await repo.consume_pairing_code(code)
        assert len(await repo.list_sessions(7)) == 1
        assert await repo.revoke_all_sessions(7) == 1
        assert await repo.list_sessions(7) == []
    run(scenario())


def test_favorites_support_local_search_and_sort(tmp_path) -> None:
    async def scenario():
        db = Database(str(tmp_path / "bot.db")); await db.initialize()
        repo = FavoritesRepository(db)
        await repo.add(1, "1", "Дюна", "Герберт")
        await repo.add(1, "2", "Ложная слепота", "Уоттс")
        rows = await repo.list(1, query="герб", sort="title")
        assert [row.book_id for row in rows] == ["1"]
        assert await repo.count(1, query="герб") == 1
    run(scenario())


def test_search_misses_are_aggregated_without_query_text(tmp_path) -> None:
    async def scenario():
        db = Database(str(tmp_path / "bot.db")); await db.initialize()
        repo = SearchStatsRepository(db)
        await repo.record_miss("секретное название")
        total, modes = await repo.summary()
        assert total == 1 and modes
        async with db.connect() as conn:
            columns = [row[1] for row in await (await conn.execute("PRAGMA table_info(search_miss_stats)")).fetchall()]
        assert "query" not in columns
    run(scenario())


def test_polite_search_prefix_is_removed_but_deterministic_fallback_remains() -> None:
    plan = build_search_plan("Пожалуйста, найди книгу Дюна", max_fallback_queries=3)
    assert plan.cleaned_query == "книгу Дюна"
    assert "Дюна" in plan.fallback_queries
