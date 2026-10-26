"""Offline search-to-book flow through the authenticated web interface."""
import asyncio
from contextlib import asynccontextmanager

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from bs4 import BeautifulSoup

from app.flibusta import AuthorResult, FlibustaError, SearchResult
from app.repositories.access import AccessRepository
from app.repositories.db import Database
from app.repositories.download_history import DownloadHistoryRepository
from app.repositories.favorites import FavoritesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.repositories.last_books import LastBooksRepository
from app.repositories.reader_settings import ReaderSettingsRepository
from app.repositories.web_access import WebAccessRepository
from app.services.search.types import SearchMode, SearchOutcome, SearchPlan
from app.web.app import WebDependencies, build_web_app


@asynccontextmanager
async def browser(tmp_path, service):
    db = Database(str(tmp_path / "search.sqlite"))
    await db.initialize()
    access = AccessRepository(db)
    await access.ensure_user(42, "approved")
    auth = WebAccessRepository(db, "test-secret")
    deps = WebDependencies(
        search_service=service, flibusta=object(), web_access_repo=auth,
        access_repo=access, favorites_repo=FavoritesRepository(db),
        history_repo=DownloadHistoryRepository(db), last_books_repo=LastBooksRepository(db),
        kindle_settings_repo=KindleSettingsRepository(db), reader_settings_repo=ReaderSettingsRepository(db),
        delivery_queue=object(), admin_ids=set(), access_control_enabled=True,
        session_days=2, max_sessions_per_user=2, download_max_bytes=1024,
        download_concurrency=1, search_rate_limit_per_minute=20,
        download_rate_limit_per_hour=5, cookie_secure=False,
    )
    client = TestClient(TestServer(build_web_app(deps)), cookie_jar=CookieJar(unsafe=True))
    await client.start_server()
    try:
        code = await auth.create_pairing_code(42)
        await client.post("/pair", data={"code": code})
        yield client
    finally:
        await client.close()


def test_web_expansion_and_pagination_preserve_query_and_show_all_results(tmp_path):
    class Service:
        def __init__(self):
            self.expansions = []

        async def search(self, query, *, expanded=False):
            self.expansions.append(expanded)
            return SearchOutcome(SearchPlan(query, query, SearchMode.EXACT, (query,), ()),
                                 [SearchResult(str(n), "Дюна", "Фрэнк Герберт") for n in range(25)],
                                 [AuthorResult(str(n), f"Автор {n}") for n in range(15)], (query,),
                                 has_more=not expanded)

    async def scenario():
        service = Service()
        async with browser(tmp_path, service) as client:
            first = BeautifulSoup(await (await client.get("/search", params={"q": 'Дюна & "<книга>"'})).text(), "html.parser")
            more = first.find("a", string="Искать дополнительные варианты")
            assert more is not None
            next_page = first.find("a", string="Дальше →")
            assert next_page is not None
            second = BeautifulSoup(await (await client.get(next_page["href"])).text(), "html.parser")
            assert second.find("a", href="/book/24") is not None
            assert second.find("a", href="/author/14") is not None
            await client.get(more["href"])
            assert service.expansions == [False, False, True]
            assert first.find("книга") is None
    asyncio.run(scenario())


def test_web_failure_keeps_query_and_does_not_claim_book_absent(tmp_path):
    class Service:
        async def search(self, query):
            raise FlibustaError("private upstream details")

    async def scenario():
        async with browser(tmp_path, Service()) as client:
            response = await client.get("/search", params={"q": "Дюна"})
            text = await response.text()
            assert response.status == 503
            assert "Источник временно недоступен" in text
            assert "Ничего не нашлось" not in text and "private upstream" not in text
            assert "Повторить поиск" in text and "Дюна" in text
    asyncio.run(scenario())


def test_web_labels_stale_cache_and_separates_approximate_results(tmp_path):
    class Service:
        async def search(self, query):
            return SearchOutcome(SearchPlan(query, query, SearchMode.EXACT, (query,), ()),
                                 [SearchResult("10", "Дюна", "Фрэнк Герберт"),
                                  SearchResult("11", "Дюны", "Автор")], [], (query,),
                                 source_unavailable=True, stale_at="2026-01-02T03:04:00+00:00")

    async def scenario():
        async with browser(tmp_path, Service()) as client:
            response = await client.get("/search", params={"q": "Дюна"}, headers={"User-Agent": "Kindle/5.0"})
            text = await response.text()
            assert "Результаты из кэша" in text and "02.01.2026" in text
            assert "Приблизительные совпадения" in text
            assert "Повторить поиск" in text
            assert "<script" not in text and "display: flex" not in text
    asyncio.run(scenario())
