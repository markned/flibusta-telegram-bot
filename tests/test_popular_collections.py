import asyncio
from datetime import datetime, timezone

import pytest

from app.flibusta import SearchResult
from app.services.popular_collections import (
    parse_ranking,
    next_refresh,
    PopularCollections,
)


def test_rankings_parse_real_metadata_without_audio_or_duplicates():
    yandex = """<script type="application/ld+json">{"@graph":[{"@type":"ItemList","itemListElement":[{"position":2,"item":{"@type":"Book","name":"Вторая","author":[{"name":"Автор"}]}},{"position":1,"item":{"@type":"Book","name":"Первая","author":[{"name":"Автор"}]}}]}]}</script>"""
    assert [(x.title, x.author) for x in parse_ranking("yandex", yandex)] == [
        ("Первая", "Автор"),
        ("Вторая", "Автор"),
    ]
    litres = """<div data-testid="art__wrapper"><a data-testid="art__title" href="/book/a/b/">Книга</a><a data-testid="art__authorName--link">Автор</a></div><div data-testid="art__wrapper"><a data-testid="art__title" href="/audiobook/a/b/">Аудио</a><a data-testid="art__authorName--link">Автор</a></div>"""
    assert [x.title for x in parse_ranking("litres", litres)] == ["Книга"]
    with pytest.raises(ValueError):
        parse_ranking("yandex", "<html>Captcha</html>")


def test_refresh_clock_is_six_belgrade_across_dst():
    for now in [
        datetime(2026, 3, 28, 12, tzinfo=timezone.utc),
        datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
    ]:
        due = next_refresh(now)
        assert due.hour == 6 and due.tzinfo.key == "Europe/Belgrade"
        assert 0 < (due - now).total_seconds() <= 86400


def test_collections_preserve_order_skip_unmatched_and_keep_snapshot_on_failure(
    tmp_path,
):
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository

    async def scenario():
        db = Database(str(tmp_path / "db.sqlite"))
        await db.initialize()

        class Client:
            calls = []

            async def popular_books(self):
                return [SearchResult("8", "Топ", "Автор")]

            async def search(self, query, limit=8):
                self.calls.append(query)
                if query == "Первая":
                    return [
                        SearchResult("1", "Первая", "Автор"),
                        SearchResult("9", "Первая", "Другой"),
                    ]
                return [SearchResult("2", "Неподходящая", "Автор")]

        async def fetch(source):
            from app.services.popular_collections import RankedBook

            return [RankedBook("Первая", "Автор"), RankedBook("Нет", "Автор")]

        service = PopularCollections(
            Client(), CacheRepository(db), fetch=fetch, pause_seconds=0
        )
        await service.refresh()
        assert [x.book_id for x in await service.books("yandex")] == ["1"]
        assert [x.book_id for x in await service.books("flibusta")] == ["8"]

        async def fail(source):
            raise TimeoutError

        service.fetch = fail
        await service.refresh()
        assert [x.book_id for x in await service.books("yandex")] == ["1"]
        assert await service.random_book() is not None

    asyncio.run(scenario())


def test_worker_is_single_and_cancels_inflight_refresh(tmp_path):
    from app.repositories.cache import CacheRepository
    from app.repositories.db import Database

    async def scenario():
        db = Database(str(tmp_path / "worker.sqlite"))
        await db.initialize()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        class Client:
            async def popular_books(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        service = PopularCollections(Client(), CacheRepository(db))
        service.start()
        first = service._task
        service.start()
        assert service._task is first
        await asyncio.wait_for(entered.wait(), 1)
        await service.stop()
        assert first.done() and cancelled.is_set() and service._task is None

    asyncio.run(scenario())


def test_snapshot_read_and_random_do_not_contact_sources(tmp_path):
    from app.repositories.cache import CacheRepository
    from app.repositories.db import Database

    async def scenario():
        db = Database(str(tmp_path / "read.sqlite"))
        await db.initialize()
        repo = CacheRepository(db)
        service = PopularCollections(object(), repo)
        assert await service.books("yandex") == []
        assert await service.random_book() is None
        await repo.set(
            "collection:flibusta",
            "collection",
            [
                {"book_id": "1", "title": "One", "author": "A"},
                {"book_id": "2", "title": "Two", "author": "B"},
            ],
            86400,
        )
        assert (await service.random_book(exclude="1")).book_id == "2"
        with pytest.raises(ValueError):
            await service.books("../unsafe")

    asyncio.run(scenario())


def test_matching_timeout_does_not_publish_partial_replacement(tmp_path):
    from app.repositories.cache import CacheRepository
    from app.repositories.db import Database
    from app.services.popular_collections import RankedBook

    async def scenario():
        db = Database(str(tmp_path / "partial.sqlite"))
        await db.initialize()
        repo = CacheRepository(db)
        await repo.set(
            "collection:yandex",
            "collection",
            [{"book_id": "5", "title": "Старый топ", "author": "Автор"}],
            86400,
        )

        class Client:
            async def popular_books(self):
                return []

            async def search(self, title, limit=12):
                if title == "Нет ответа":
                    raise TimeoutError
                return [SearchResult("1", "Первая", "Автор")]

        async def fetch(source):
            return [RankedBook("Первая", "Автор"), RankedBook("Нет ответа", "Автор")]

        service = PopularCollections(Client(), repo, fetch=fetch, pause_seconds=0)
        await service.refresh()
        assert [b.book_id for b in await service.books("yandex")] == ["5"]

    asyncio.run(scenario())


def test_random_one_book_still_works_when_repeated(tmp_path):
    from app.repositories.cache import CacheRepository
    from app.repositories.db import Database

    async def scenario():
        db = Database(str(tmp_path / "one.sqlite"))
        await db.initialize()
        repo = CacheRepository(db)
        await repo.set(
            "collection:flibusta",
            "collection",
            [{"book_id": "1", "title": "One", "author": "A"}],
            86400,
        )
        result = await PopularCollections(object(), repo).random_book(exclude="1")
        assert result and result.book_id == "1"

    asyncio.run(scenario())


def test_external_fetch_rejects_untrusted_redirect_and_oversized_body(monkeypatch):
    import httpx
    import app.services.popular_collections as module

    original = httpx.AsyncClient

    async def scenario():
        for response in [
            httpx.Response(302, headers={"location": "https://evil.test/"}),
            httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)),
        ]:
            monkeypatch.setattr(
                module.httpx,
                "AsyncClient",
                lambda **kwargs: original(
                    transport=httpx.MockTransport(lambda request: response), **kwargs
                ),
            )
            with pytest.raises(ValueError):
                await module.fetch_ranking("yandex")

    asyncio.run(scenario())
