import asyncio
import json

from app.flibusta import SearchResult, parse_author_page
from app.services.works import group_books, choose_default, edition_order


def test_editions_volumes_and_roman_numbers_group_without_losing_ids():
    books = [
        SearchResult("1", "Война и мир. Том III", "Лев Толстой"),
        SearchResult("2", "Война и мир. Том 3 [litres]", "Лев Толстой"),
        SearchResult("3", "Война и мир", "Лев Толстой"),
        SearchResult("4", "Война и мир (пер. Иванов)", "Лев Толстой"),
    ]
    works = group_books(books)
    assert len(works) == 1 and works[0].title == "Война и мир"
    assert {e.book_id for e in works[0].editions} == {"1", "2", "3", "4"}
    assert choose_default(works[0]).book_id == "3"
    assert edition_order(works[0].editions[0]) == edition_order(works[0].editions[1])


def test_parts_do_not_masquerade_as_complete_book():
    works = group_books(
        [
            SearchResult("1", "Анна Каренина. Том 1. Части 1 - 4", "Лев Толстой"),
            SearchResult("2", "Анна Каренина. Части 5-6", "Лев Толстой"),
        ]
    )
    assert len(works) == 1 and choose_default(works[0]) is None
    assert len(works[0].editions) == 2


def test_notes_other_authors_and_collections_stay_separate():
    titles = [
        "Анна Каренина",
        "Планы и заметки к «Анне Карениной»",
        "Варианты к «Анне Карениной»",
        "Анна Каренина (сборник)",
    ]
    books = [
        SearchResult(str(i), title, "Лев Толстой") for i, title in enumerate(titles)
    ]
    books.append(SearchResult("5", "Анна Каренина", "Другой автор"))
    assert len(group_books(books)) == 5
    assert (
        len(
            group_books(
                [SearchResult("6", "Книга", "Автор"), SearchResult("7", "Книга", "")]
            )
        )
        == 2
    )


def test_order_is_alphabetic_only_when_requested_and_grouping_is_idempotent():
    books = [
        SearchResult("1", "Я", "Автор"),
        SearchResult("2", "А", "Автор"),
        SearchResult("3", "Я [litres]", "Автор"),
    ]
    assert [w.title for w in group_books(books)] == ["Я", "А"]
    assert [w.title for w in group_books(books, alphabetical=True)] == ["А", "Я"]
    assert group_books(group_books(books)) == group_books(books)


def test_cached_editions_roundtrip():
    from dataclasses import asdict

    work = group_books(
        [
            SearchResult("1", "Книга", "Автор"),
            SearchResult("2", "Книга [litres]", "Автор"),
        ]
    )[0]
    restored = SearchResult(**json.loads(json.dumps(asdict(work))))
    assert restored == work and restored.editions[1].book_id == "2"


def test_author_parser_can_read_beyond_forty_without_download_details():
    markup = (
        '<div id="main"><h1>Автор</h1><ul>'
        + "".join(f'<li><a href="/b/{n}">Книга {n}</a></li>' for n in range(97))
        + "</ul></div>"
    )
    _, books = parse_author_page(markup, "9", limit=None)
    assert len(books) == 97


def test_author_work_pages_keep_all_editions_and_continue_past_five_pages(tmp_path):
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository
    from app.services.cached_flibusta import CachedFlibustaClient

    async def scenario():
        db = Database(str(tmp_path / "works.sqlite"))
        await db.initialize()

        class Client:
            calls = 0

            async def author_books(self, author_id, limit=40):
                self.calls += 1
                assert limit is None
                return "Автор", [
                    SearchResult(str(i), f"Книга {i:03}", "Автор") for i in range(113)
                ]

        raw = Client()
        client = CachedFlibustaClient(
            raw, CacheRepository(db), enabled=True, ttls={"author_books": 86400}
        )
        first = await client.author_works_page("9")
        second = await client.author_works_page("9", offset=40)
        last = await client.author_works_page("9", offset=80)
        assert first.total == 113 and first.has_more and len(first.books) == 40
        assert second.books[0].book_id == "40" and second.has_more
        assert len(last.books) == 33 and not last.has_more
        assert raw.calls == 1

    asyncio.run(scenario())


def test_web_work_registry_is_owner_bound_and_expires():
    from app.services.work_registry import WorkRegistry

    registry = WorkRegistry(max_entries=2, max_editions=4, ttl=10)
    work = group_books(
        [
            SearchResult("1", "Книга", "Автор"),
            SearchResult("2", "Книга [litres]", "Автор"),
        ]
    )[0]
    token = registry.add(42, work, "/author/9?page=5", now=100)
    assert registry.get(token, 42, now=105).return_url == "/author/9?page=5"
    assert registry.get(token, 43, now=105) is None
    assert registry.get(token, 42, now=111) is None
    tokens = [registry.add(42, work, "/search?q=x", now=120) for _ in range(3)]
    assert len(registry.entries) == 2 and registry.get(tokens[0], 42, now=121) is None


def test_bounded_author_html_handles_compressed_source():
    import gzip
    import httpx
    from app.flibusta import FlibustaClient

    async def scenario():
        html = (
            b'<div id="main"><h1>Author</h1><form><a href="/b/42">Book</a></form></div>'
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    content=gzip.compress(html),
                    headers={
                        "content-encoding": "gzip",
                        "content-type": "text/html; charset=utf-8",
                    },
                )
            )
        ) as http:
            _, books = await FlibustaClient(
                "https://catalog.test", client=http
            ).author_books("9", limit=None)
            assert [b.book_id for b in books] == ["42"]

    asyncio.run(scenario())


def test_complete_work_fetches_other_editions_only_on_demand(tmp_path):
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository
    from app.services.cached_flibusta import CachedFlibustaClient

    async def scenario():
        db = Database(str(tmp_path / "completion.sqlite"))
        await db.initialize()

        class Client:
            calls = 0

            async def author_books(self, author_id, limit=None):
                self.calls += 1
                return "Автор", [
                    SearchResult("1", "Роман", "Автор"),
                    SearchResult("2", "Роман. Том 1", "Автор"),
                    SearchResult("3", "Планы и заметки к роману", "Автор"),
                ]

        raw = Client()
        client = CachedFlibustaClient(
            raw, CacheRepository(db), enabled=True, ttls={"author_books": 86400}
        )
        work = SearchResult("1", "Роман", "Автор", source_author_id="9")
        assert raw.calls == 0
        completed = await client.complete_work(work)
        assert {e.book_id for e in completed.editions} == {"1", "2"}
        await client.complete_work(work)
        assert raw.calls == 1

    asyncio.run(scenario())


def test_leading_tome_and_explicit_draft_stay_with_work_without_becoming_default():
    books = [
        SearchResult("1", "Том 4. Война и мир", "Лев Толстой"),
        SearchResult("2", "Война и мир. Первый вариант романа", "Лев Толстой"),
    ]
    works = group_books(books)
    assert len(works) == 1 and works[0].title == "Война и мир"
    assert choose_default(works[0]) is None
    full = group_books([*books, SearchResult("3", "Война и мир", "Лев Толстой")])[0]
    assert choose_default(full).book_id == "3"


def test_registry_enrichment_respects_total_edition_budget():
    from app.services.work_registry import WorkRegistry
    from app.flibusta import EditionRef

    registry = WorkRegistry(max_entries=3, max_editions=3)
    work = SearchResult("1", "Роман", "Автор")
    first = registry.add(42, work, "/search?q=x", now=100)
    second = registry.add(42, work, "/search?q=x", now=100)
    expanded = SearchResult(
        "1",
        "Роман",
        "Автор",
        tuple(EditionRef(str(i), "Роман", "Автор") for i in range(3)),
    )
    assert registry.set_work(first, 42, expanded, now=101).work == expanded
    assert registry.get(second, 42, now=101) is None
    assert len(registry.entries) == 1


def test_unbounded_author_parse_rejects_unrecognizable_source():
    import pytest
    from app.flibusta import FlibustaError

    with pytest.raises(FlibustaError):
        parse_author_page("<html><p>captcha</p></html>", "9", limit=None)


def test_complete_work_uses_confirmed_author_identity_not_name_spelling(tmp_path):
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository
    from app.services.cached_flibusta import CachedFlibustaClient

    async def scenario():
        db = Database(str(tmp_path / "identity.sqlite"))
        await db.initialize()

        class Client:
            async def author_books(self, author_id, limit=None):
                return "Лев Николаевич Толстой", [
                    SearchResult("1", "Роман", "Лев Николаевич Толстой"),
                    SearchResult("2", "Роман [litres]", "Лев Николаевич Толстой"),
                ]

        client = CachedFlibustaClient(
            Client(), CacheRepository(db), enabled=True, ttls={"author_books": 86400}
        )
        work = await client.complete_work(
            SearchResult("1", "Роман", "Лев Толстой", source_author_id="9")
        )
        assert {e.book_id for e in work.editions} == {"1", "2"}

    asyncio.run(scenario())


def test_author_search_propagates_real_total_and_continuation(tmp_path):
    from app.flibusta import AuthorResult
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository
    from app.services.cached_flibusta import CachedFlibustaClient
    from app.services.search import SearchService

    async def scenario():
        db = Database(str(tmp_path / "service.sqlite"))
        await db.initialize()

        class Client:
            async def search_authors(self, query, limit=20):
                return [AuthorResult("9", "Лев Толстой")]

            async def author_books(self, author_id, limit=None):
                return "Лев Толстой", [
                    SearchResult(str(i + 1), f"Произведение {i:03}", "Лев Толстой")
                    for i in range(113)
                ]

        client = CachedFlibustaClient(
            Client(),
            CacheRepository(db),
            enabled=True,
            ttls={"author_books": 86400, "author_search": 86400},
        )
        outcome = await SearchService(client, book_limit=8, author_limit=20).search(
            "Лев Толстой"
        )
        assert outcome.author_total == 113 and outcome.author_has_more
        assert (
            outcome.author_id == "9"
            and outcome.author_offset == 40
            and len(outcome.books) == 40
        )

    asyncio.run(scenario())


def test_author_metadata_size_bound_is_explicit_not_a_silent_short_list():
    import httpx
    import pytest
    from app.flibusta import FlibustaClient, FlibustaError

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
            )
        ) as http:
            with pytest.raises(FlibustaError, match="слишком большой"):
                await FlibustaClient(
                    "https://catalog.test", client=http, retries=1
                ).author_books("9", limit=None)

    asyncio.run(scenario())
