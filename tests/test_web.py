from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from app.flibusta import AuthorResult, BookDetails, DownloadFormat, SearchResult
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


def run(coro):
    return asyncio.run(coro)


def test_web_pairing_code_is_one_time_and_tokens_are_not_stored_raw(tmp_path: Path) -> None:
    async def scenario():
        db = Database(str(tmp_path / "web.sqlite"))
        await db.initialize()
        repo = WebAccessRepository(db, "test-secret")
        code = await repo.create_pairing_code(42, ttl_seconds=600)
        token = await repo.consume_pairing_code(code, session_days=2, max_sessions_per_user=2)
        assert token is not None
        assert await repo.consume_pairing_code(code) is None
        session = await repo.get_session(token)
        assert session is not None and session.user_id == 42
        async with db.connect() as conn:
            code_row = await (await conn.execute("SELECT code_hash FROM web_pairing_codes")).fetchone()
            token_row = await (await conn.execute("SELECT token_hash FROM web_sessions")).fetchone()
        assert code.replace("-", "") not in code_row["code_hash"]
        assert token not in token_row["token_hash"]
        await repo.revoke_session(token)
        assert await repo.get_session(token) is None

    run(scenario())


def test_web_shell_login_search_book_download_and_send(tmp_path: Path) -> None:
    class FakeSearch:
        def __init__(self):
            self.queries = []

        async def search(self, query):
            self.queries.append(query)
            plan = SearchPlan(query, query, SearchMode.EXACT, (query,), ())
            return SearchOutcome(
                plan,
                [SearchResult("10", "Дюна", "Фрэнк Герберт")],
                [AuthorResult("20", "Фрэнк Герберт")],
                (query,),
            )

    class FakeFlibusta:
        def __init__(self):
            self.details_calls = 0

        async def details(self, book_id):
            self.details_calls += 1
            return BookDetails(
                book_id=book_id,
                title="Дюна",
                authors=["Фрэнк Герберт"],
                author_refs=[AuthorResult("20", "Фрэнк Герберт")],
                translators=[],
                illustrators=[],
                genres=["Фантастика"],
                file_size="1 МБ",
                pages=500,
                annotation="Пустынная планета Арракис.",
                formats=[DownloadFormat("epub", "EPUB", "https://example.test/dune.epub")],
                page_url="https://example.test/b/10",
            )

        async def author_books(self, author_id, limit=40):
            return "Фрэнк Герберт", [SearchResult("10", "Дюна", "Фрэнк Герберт")]

        async def download(self, url, max_bytes):
            return b"epub-content", "Фрэнк Герберт - Дюна.epub", "application/epub+zip"

    class FakeQueue:
        def __init__(self):
            self.jobs = []

        async def enqueue(self, **kwargs):
            self.jobs.append(kwargs)
            return 1

    async def scenario():
        db = Database(str(tmp_path / "web.sqlite"))
        await db.initialize()
        access = AccessRepository(db)
        await access.ensure_user(42, "approved")
        web_access = WebAccessRepository(db, "test-secret")
        code = await web_access.create_pairing_code(42)
        search = FakeSearch()
        flibusta = FakeFlibusta()
        queue = FakeQueue()
        kindle_settings = KindleSettingsRepository(db)
        await kindle_settings.upsert(42, "reader@kindle.com")
        deps = WebDependencies(
            search_service=search,
            flibusta=flibusta,
            web_access_repo=web_access,
            access_repo=access,
            favorites_repo=FavoritesRepository(db),
            history_repo=DownloadHistoryRepository(db),
            last_books_repo=LastBooksRepository(db),
            kindle_settings_repo=kindle_settings,
            reader_settings_repo=ReaderSettingsRepository(db),
            delivery_queue=queue,
            admin_ids=set(),
            access_control_enabled=True,
            session_days=90,
            max_sessions_per_user=5,
            download_max_bytes=45 * 1024 * 1024,
            download_concurrency=1,
            search_rate_limit_per_minute=20,
            download_rate_limit_per_hour=30,
            cookie_secure=False,
        )
        client = TestClient(TestServer(build_web_app(deps)), cookie_jar=CookieJar(unsafe=True))
        await client.start_server()
        try:
            login = await client.get("/")
            assert "Полка — вход" in await login.text()

            paired = await client.post("/pair", data={"code": code}, allow_redirects=False)
            assert paired.status == 303
            home = await client.get("/")
            assert "Найти книгу" in await home.text()

            found = await client.get("/search", params={"q": "Дюна"})
            text = await found.text()
            assert found.status == 200 and "Дюна" in text and "/book/10" in text
            assert search.queries == ["Дюна"]

            book = await client.get("/book/10")
            assert "Пустынная планета" in await book.text()

            kindle_book = await client.get(
                "/book/10",
                headers={"User-Agent": "Mozilla/5.0 Kindle/5.17.1"},
            )
            kindle_text = await kindle_book.text()
            assert "Добавить на Kindle" in kindle_text
            assert "/download/" not in kindle_text

            downloaded = await client.get("/download/10/epub")
            assert downloaded.status == 200
            assert await downloaded.read() == b"epub-content"
            assert "attachment" in downloaded.headers["Content-Disposition"]

            recent_home = await client.get("/")
            recent_text = await recent_home.text()
            assert "Недавние книги" in recent_text and "/book/10" in recent_text

            sent = await client.post("/send/kindle/10", allow_redirects=False)
            assert sent.status == 303
            assert sent.headers["Location"] == "/sent/kindle/10"
            assert queue.jobs[0]["chat_id"] is None
            assert queue.jobs[0]["status_message_id"] is None

            sent_page = await client.get(sent.headers["Location"])
            sent_text = await sent_page.text()
            assert "Готово" in sent_text and "Дюна" in sent_text

            history = await client.get("/history")
            history_text = await history.text()
            assert "Дюна" in history_text and "браузер" in history_text
        finally:
            await client.close()

    run(scenario())


def test_web_protected_routes_redirect_without_session(tmp_path: Path) -> None:
    async def scenario():
        db = Database(str(tmp_path / "web.sqlite"))
        await db.initialize()
        access = AccessRepository(db)
        deps = WebDependencies(
            search_service=object(),
            flibusta=object(),
            web_access_repo=WebAccessRepository(db, "secret"),
            access_repo=access,
            favorites_repo=FavoritesRepository(db),
            history_repo=DownloadHistoryRepository(db),
            last_books_repo=LastBooksRepository(db),
            kindle_settings_repo=KindleSettingsRepository(db),
            reader_settings_repo=ReaderSettingsRepository(db),
            delivery_queue=object(),
            admin_ids=set(),
            access_control_enabled=True,
            session_days=90,
            max_sessions_per_user=5,
            download_max_bytes=1024,
            download_concurrency=1,
            search_rate_limit_per_minute=20,
            download_rate_limit_per_hour=30,
            cookie_secure=False,
        )
        client = TestClient(TestServer(build_web_app(deps)))
        await client.start_server()
        try:
            response = await client.get("/favorites", allow_redirects=False)
            assert response.status == 303 and response.headers["Location"] == "/"
        finally:
            await client.close()

    run(scenario())
