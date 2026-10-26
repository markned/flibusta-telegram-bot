"""Catalog evidence, not a list of famous writers, resolves natural queries."""

import asyncio

import pytest

from app.flibusta import AuthorResult, SearchResult
from app.services.author_names import names_match
from app.services.search import SearchMode, SearchService


class Catalog:
    def __init__(self):
        self.requests = []

    async def search_authors(self, query, limit=20):
        self.requests.append(("authors", query))
        if query.casefold() in {"умберто эко", "эко", "умберто"}:
            return [AuthorResult("10", "Умберто Эко")]
        if query.casefold() in {"ремарк", "эрих мария ремарк"}:
            return [AuthorResult("11", "Эрих Мария Ремарк")]
        return []

    async def author_books(self, author_id, limit=40):
        self.requests.append(("works", author_id))
        if author_id == "10":
            return "Умберто Эко", [SearchResult("100", "Имя розы", "Умберто Эко")]
        return "Эрих Мария Ремарк", [
            SearchResult("101", "Три товарища", "Эрих Мария Ремарк")
        ]

    async def search(self, query, limit=40):
        self.requests.append(("books", query))
        if query.casefold() in {
            "имя розы",
            "эко имя розы",
            "розы имя эко",
            "имя розы умберто эко",
        }:
            return [SearchResult("100", "Имя розы", "Умберто Эко")]
        if query.casefold() in {"три товарища", "ремарка три товарища"}:
            return [SearchResult("101", "Три товарища", "Эрих Мария Ремарк")]
        return []


def engine(client):
    return SearchService(client, book_limit=40, author_limit=20, timeout_seconds=0.3)


@pytest.mark.parametrize(
    "query", ["умберто эко", "Эко", "книги Умберто Эко", "книги Эриха Марии Ремарка"]
)
def test_unlisted_author_is_resolved_from_catalog(query):
    client = Catalog()
    result = asyncio.run(engine(client).search(query))
    assert result.plan.mode == SearchMode.AUTHOR
    assert result.books and result.authors
    assert not result.approximate


@pytest.mark.parametrize(
    "query",
    ["эко имя розы", "розы имя эко", "имя розы умберто эко", "ремарка три товарища"],
)
def test_catalog_metadata_separates_unlisted_author_and_title(query):
    result = asyncio.run(engine(Catalog()).search(query))
    assert result.plan.mode == SearchMode.AUTHOR_TITLE
    assert [b.book_id for b in result.books] == (
        ["101"] if "ремарк" in query else ["100"]
    )
    assert result.plan.author and result.plan.title


def test_unknown_author_typo_uses_other_name_anchor_and_requires_confirmation():
    result = asyncio.run(engine(Catalog()).search("умберто экоо"))
    assert result.suggested_query == "Умберто Эко"
    assert not result.books


def test_author_title_collision_asks_one_question():
    class Client(Catalog):
        async def search(self, query, limit=40):
            return [SearchResult("102", "Умберто Эко", "Другой писатель")]

    result = asyncio.run(engine(Client()).search("умберто эко"))
    assert not result.books
    assert "книги Умберто Эко" in result.query_choices
    assert "«умберто эко»" in result.query_choices


def test_title_typo_can_use_bounded_previously_observed_catalog_hints():
    class Client(Catalog):
        async def catalog_hints(self):
            return [SearchResult("100", "Имя розы", "Умберто Эко")], []

    result = asyncio.run(engine(Client()).search("Имя рзоы"))
    assert result.suggested_query == "Имя розы"
    assert not result.books


def test_slow_optional_author_lookup_does_not_consume_search_deadline():
    class Client(Catalog):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def search_authors(self, query, limit=20):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    async def scenario():
        client = Client()
        result = await asyncio.wait_for(engine(client).search("Имя розы"), timeout=0.25)
        assert result.books[0].book_id == "100"
        assert client.cancelled
        assert not result.source_unavailable

    asyncio.run(scenario())


def test_inflection_matching_uses_actual_catalog_name_and_keeps_given_name():
    assert names_match("Эриха Марии Ремарка", "Эрих Мария Ремарк")
    assert not names_match("Эрих Ремарк", "Алексей Ремарк")
    assert not names_match("Ремарк", "Ремарков")


@pytest.mark.parametrize(
    "query", ["похожее на Дюну", "интересное про жизнь", "что-нибудь покороче"]
)
def test_unsupported_semantic_requests_do_not_become_literal_book_search(query):
    client = Catalog()
    result = asyncio.run(engine(client).search(query))
    assert result.plan.mode == SearchMode.UNSUPPORTED_TOPIC
    assert not client.requests


def test_collision_is_independent_of_source_completion_order():
    class Client(Catalog):
        async def search(self, query, limit=40):
            await asyncio.sleep(0.01)
            return [SearchResult("102", "Умберто Эко", "Другой писатель")]

    result = asyncio.run(engine(Client()).search("умберто эко"))
    assert not result.books and result.query_choices


def test_request_budget_stays_bounded_on_catalog_miss():
    client = Catalog()
    result = asyncio.run(engine(client).search("неизвестное название книги"))
    assert not result.books
    assert sum(kind == "books" for kind, _ in client.requests) <= 4
    assert sum(kind == "authors" for kind, _ in client.requests) <= 4
