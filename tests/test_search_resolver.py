import asyncio

import pytest

from app.flibusta import AuthorResult, FlibustaClient, SearchResult
from app.services.search_logic import rank_and_dedupe_books
from app.services.search_resolver import resolve_search


def run(coro):
    return asyncio.run(coro)


def test_combined_search_starts_book_and_author_requests_together() -> None:
    class Response:
        text = "<div id='main'></div>"

    class Client(FlibustaClient):
        def __init__(self):
            super().__init__("https://flibusta.is", client=object())
            self.started = 0
            self.both_started = asyncio.Event()

        async def _get(self, url: str):
            self.started += 1
            if self.started == 2:
                self.both_started.set()
            await asyncio.wait_for(self.both_started.wait(), timeout=0.2)
            return Response()

    async def scenario():
        client = Client()
        await client.search_all("Дюна")
        return client.started

    assert run(scenario()) == 2


def test_search_resolver_uses_bounded_fallback() -> None:
    class Client:
        def __init__(self):
            self.queries = []

        async def search_all(self, query, book_limit, author_limit):
            self.queries.append(query)
            if query == "Исповедь":
                return [SearchResult("1", "Исповедь", "Лев Толстой")], []
            return [], []

    client = Client()
    result = run(resolve_search(client, "Исповедь Толстой", book_limit=10, author_limit=10, max_fallback_queries=1))
    assert result.books[0].book_id == "1"
    assert client.queries == ["Исповедь Толстой", "Исповедь"]


def test_search_resolver_enforces_deadline() -> None:
    class Client:
        async def search_all(self, *args, **kwargs):
            await asyncio.sleep(0.1)
            return [], []

    with pytest.raises(TimeoutError):
        run(resolve_search(Client(), "Дюна", book_limit=10, author_limit=10, timeout_seconds=0.01))


def test_book_ranking_prefers_full_token_match() -> None:
    results = [
        SearchResult("weak", "Мастер", "Другой автор"),
        SearchResult("best", "Мастер и Маргарита", "Михаил Булгаков"),
    ]
    ranked = rank_and_dedupe_books(results, "мастер и маргарита")
    assert ranked[0].book_id == "best"
