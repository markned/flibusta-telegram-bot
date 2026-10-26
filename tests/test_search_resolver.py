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


def test_duplicate_editions_are_collapsed_and_clean_copy_wins() -> None:
    results = [
        SearchResult("litres", "1984 [litres с оптимизированной обложкой]", "Джордж Оруэлл"),
        SearchResult("clean", "1984", "Джордж Оруэлл"),
        SearchResult("other", "1984", "Другой автор"),
    ]
    ranked = rank_and_dedupe_books(results, "1984")
    assert [item.book_id for item in ranked] == ["clean", "other"]


def test_search_continues_with_bounded_fallback_after_primary_failure() -> None:
    class Client:
        def __init__(self):
            self.queries = []

        async def search(self, query, limit):
            self.queries.append(query)
            if query == "книга Дюна":
                raise TimeoutError
            return [SearchResult("dune", "Дюна", "Фрэнк Герберт")]

        async def search_authors(self, query, limit):
            return []

    client = Client()
    service = SearchService(client, book_limit=10, author_limit=10, max_fallback_queries=2)
    result = run(service.search("книга Дюна"))
    assert result.books[0].book_id == "dune"
    assert client.queries == ["книга Дюна", "Дюна"]


def test_keyboard_layout_correction_is_bounded() -> None:
    assert correct_keyboard_layout("l.yf") == "дюна"
    plan = build_search_plan("l.yf")
    assert plan.primary_queries == ("l.yf", "дюна")
    assert correct_keyboard_layout("Дюна") is None


def test_author_and_title_matching_helpers() -> None:
    assert author_name_matches("толстой", "Лев Николаевич Толстой")
    assert title_matches("Преступление и наказание", "Преступление и наказание [litres]")


def test_author_title_rejects_other_titles_by_same_author():
    class Client:
        async def search(self, query, limit):
            return [SearchResult("101", "Мессия Дюны", "Фрэнк Герберт")]

        async def search_authors(self, query, limit):
            return []

    service = SearchService(Client(), book_limit=10, author_limit=10)
    result = run(service.search_author_title("Фрэнк Герберт", "Дюна"))
    assert result.books == []


def test_author_title_preserves_author_when_general_fallback_is_empty():
    class Client:
        async def search(self, query, limit):
            return []

        async def search_authors(self, query, limit):
            return [AuthorResult("42", "Лев Толстой")]

        async def author_books(self, author_id, limit):
            return "Лев Толстой", []

    service = SearchService(Client(), book_limit=10, author_limit=10)
    result = run(service.search("исповедь толстой"))
    assert [author.author_id for author in result.authors] == ["42"]


def test_expanded_search_keeps_earlier_candidates_when_fallback_times_out():
    class Client:
        async def search(self, query, limit):
            if query == "Очень длинное название книги автора":
                return [SearchResult("102", "Очень длинное название", "Автор")]
            await asyncio.Event().wait()

    service = SearchService(Client(), book_limit=10, author_limit=10, timeout_seconds=0.05)
    result = run(service.search("Очень длинное название книги автора", expanded=True))
    assert [book.book_id for book in result.books] == ["102"]
    assert result.partial is True
    assert result.source_unavailable is True


def test_first_approximate_results_do_not_wait_for_optional_fallback():
    class Client:
        def __init__(self):
            self.queries = []

        async def search(self, query, limit):
            self.queries.append(query)
            if len(self.queries) > 1:
                raise AssertionError("optional expansion must require a separate request")
            return [SearchResult("102", "Очень длинное название", "Автор")]

    client = Client()
    service = SearchService(client, book_limit=10, author_limit=10)
    result = run(service.search("Очень длинное название книги автора"))
    assert result.books[0].book_id == "102"
    assert result.approximate is True
    assert result.has_more is True
    assert client.queries == ["Очень длинное название книги автора"]


def test_expanded_author_title_does_not_relax_author_constraint():
    class Client:
        async def search(self, query, limit):
            return [SearchResult("103", "Исповедь", "Другой автор")]

        async def search_authors(self, query, limit):
            return []

    service = SearchService(Client(), book_limit=10, author_limit=10)
    assert run(service.search("исповедь толстой", expanded=True)).books == []


@pytest.mark.parametrize("query", ["Неадине с собой", "Вчееринка в Хэллоуин"])
def test_preposition_before_last_word_is_not_treated_as_author(query):
    assert build_search_plan(query).mode == SearchMode.EXACT


@pytest.mark.parametrize("author,title", [
    ("Марк Аврелий", "Наедине с собой"),
    ("Агата Кристи", "Вечеринка в Хэллоуин"),
    ("Джон Стрелеки", "Кафе на краю земли. Как перестать плыть по течению"),
])
def test_explicit_author_title_supports_unlisted_authors_and_long_titles(author, title):
    plan = build_search_plan(f"{author} — {title}")
    assert plan.mode == SearchMode.AUTHOR_TITLE
    assert plan.author == author
    assert plan.title == title


def test_external_cancellation_is_not_converted_to_partial_results():
    class Client:
        async def search(self, query, limit):
            await asyncio.Event().wait()

    async def scenario():
        service = SearchService(Client(), book_limit=10, author_limit=10)
        task = asyncio.create_task(service.search("Дюна"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(scenario())


def test_explicit_author_title_accepts_lowercase_unlisted_author():
    plan = build_search_plan("агата кристи — вечеринка в хэллоуин")
    assert plan.author == "агата кристи"
    assert plan.title == "вечеринка в хэллоуин"
