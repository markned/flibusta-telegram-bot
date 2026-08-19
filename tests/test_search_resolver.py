import asyncio

import pytest

from app.flibusta import AuthorResult, FlibustaClient, SearchResult
from app.services.search import SearchMode, SearchService
from app.services.search.planner import build_search_plan, correct_keyboard_layout
from app.services.search.service import author_name_matches, title_matches
from app.services.search_logic import rank_and_dedupe_books


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


def test_general_search_uses_bounded_fallback() -> None:
    class Client:
        def __init__(self):
            self.queries = []

        async def search(self, query, limit):
            self.queries.append(query)
            if query == "Очень длинное название книги":
                return [SearchResult("1", "Очень длинное название книги", "Автор")]
            return []

        async def search_authors(self, query, limit):
            return []

    client = Client()
    service = SearchService(client, book_limit=10, author_limit=10, max_fallback_queries=1)
    result = run(service.search("Очень длинное название книги автора"))
    assert result.books[0].book_id == "1"
    assert client.queries == ["Очень длинное название книги автора", "Очень длинное название книги"]


def test_search_service_enforces_total_deadline() -> None:
    class Client:
        async def search(self, *args, **kwargs):
            await asyncio.sleep(0.1)
            return []

    service = SearchService(Client(), book_limit=10, author_limit=10, timeout_seconds=0.01)
    with pytest.raises(TimeoutError):
        run(service.search("Дюна"))


def test_author_title_search_is_case_insensitive_and_filters_wrong_author() -> None:
    class Client:
        async def search(self, query, limit):
            return [
                SearchResult("bad", "Исповедь королевы", "Другой автор"),
                SearchResult("ok", "Исповедь", "Лев Толстой"),
            ]

        async def search_all(self, query, book_limit, author_limit):
            return [], []

        async def search_authors(self, query, limit):
            return []

    service = SearchService(Client(), book_limit=10, author_limit=10)
    result = run(service.search("исповедь толстой"))
    assert result.plan.mode == SearchMode.AUTHOR_TITLE
    assert [book.book_id for book in result.books] == ["ok"]


def test_author_title_can_expand_author_catalog() -> None:
    class Client:
        async def search(self, query, limit):
            return [SearchResult("bad", "Исповедь королевы", "Другой автор")]

        async def search_all(self, query, book_limit, author_limit):
            return [], [AuthorResult("42", "Лев Толстой")]

        async def search_authors(self, query, limit):
            return [AuthorResult("42", "Лев Толстой")]

        async def author_books(self, author_id, limit):
            return "Лев Толстой", [
                SearchResult("ok", "Исповедь", "Лев Толстой"),
                SearchResult("other", "Война и мир", "Лев Толстой"),
            ]

    service = SearchService(Client(), book_limit=10, author_limit=10)
    result = run(service.search("толстой исповедь"))
    assert [book.book_id for book in result.books] == ["ok"]


def test_author_title_tolerates_partial_endpoint_failure() -> None:
    class Client:
        async def search(self, query, limit):
            return [SearchResult("ok", "Идиот", "Федор Достоевский")]

        async def search_all(self, *args, **kwargs):
            raise RuntimeError("temporary")

        async def search_authors(self, *args, **kwargs):
            raise RuntimeError("temporary")

    service = SearchService(Client(), book_limit=10, author_limit=10)
    result = run(service.search("идиот достоевский"))
    assert result.books[0].book_id == "ok"


def test_misclassified_person_like_title_falls_back_to_general_search() -> None:
    class Client:
        async def search(self, query, limit):
            if query.casefold() == "евгений онегин":
                return [SearchResult("onegin", "Евгений Онегин", "Александр Пушкин")]
            return []

        async def search_authors(self, query, limit):
            return []

    service = SearchService(Client(), book_limit=10, author_limit=10)
    result = run(service.search("Евгений Онегин"))
    assert result.books[0].book_id == "onegin"


def test_exact_title_does_not_wait_for_author_endpoint() -> None:
    class Client:
        def __init__(self):
            self.author_calls = 0

        async def search(self, query, limit):
            return [SearchResult("book", "Чапаев и пустота", "Виктор Пелевин")]

        async def search_authors(self, query, limit):
            self.author_calls += 1
            await asyncio.sleep(1)
            return []

    client = Client()
    service = SearchService(client, book_limit=10, author_limit=10, timeout_seconds=0.05)
    result = run(service.search("Чапаев и пустота"))
    assert result.books[0].book_id == "book"
    assert client.author_calls == 0


def test_author_title_returns_direct_match_without_author_lookup() -> None:
    class Client:
        def __init__(self):
            self.author_calls = 0

        async def search(self, query, limit):
            return [SearchResult("book", "Исповедь", "Лев Толстой")]

        async def search_authors(self, query, limit):
            self.author_calls += 1
            await asyncio.sleep(1)
            return []

    client = Client()
    service = SearchService(client, book_limit=10, author_limit=10, timeout_seconds=0.05)
    result = run(service.search("исповедь толстой"))
    assert result.books[0].book_id == "book"
    assert client.author_calls == 0


def test_book_ranking_prefers_full_token_match() -> None:
    results = [
        SearchResult("weak", "Мастер", "Другой автор"),
        SearchResult("best", "Мастер и Маргарита", "Михаил Булгаков"),
    ]
    ranked = rank_and_dedupe_books(results, "мастер и маргарита")
    assert ranked[0].book_id == "best"


def test_keyboard_layout_correction_is_bounded() -> None:
    assert correct_keyboard_layout("l.yf") == "дюна"
    plan = build_search_plan("l.yf")
    assert plan.primary_queries == ("l.yf", "дюна")
    assert correct_keyboard_layout("Дюна") is None


def test_author_and_title_matching_helpers() -> None:
    assert author_name_matches("толстой", "Лев Николаевич Толстой")
    assert title_matches("Преступление и наказание", "Преступление и наказание [litres]")
