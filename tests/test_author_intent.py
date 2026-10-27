"""Author intent must never silently become a search for books about the name."""

import asyncio

import pytest

from app.flibusta import AuthorResult, FlibustaError, SearchResult
from app.services.search import SearchMode, SearchService
from app.services.search.planner import build_search_plan
from app.services.search.service import author_name_matches


class Catalog:
    def __init__(self, authors=(), *, fail=False):
        self.authors = list(authors)
        self.fail = fail
        self.book_queries = []
        self.author_queries = []
        self.expanded = []

    async def search_authors(self, query, limit=20):
        self.author_queries.append(query)
        if self.fail:
            raise FlibustaError("source unavailable")
        return self.authors

    async def search(self, query, limit=40):
        self.book_queries.append(query)
        return [
            SearchResult("100", "Лев Толстой. Биография", "Павел Басинский"),
            SearchResult("101", "Толстой", "Дмитрий Быков"),
        ]

    async def author_books(self, author_id, limit=40):
        self.expanded.append(author_id)
        return "Лев Николаевич Толстой", [
            SearchResult("200", "Война и мир", "Лев Толстой"),
            SearchResult("201", "Лев Толстой. Биография", "Павел Басинский"),
            SearchResult("202", "Исповедь", None),
        ]


def service(client):
    return SearchService(client, book_limit=40, author_limit=20, timeout_seconds=0.2)


@pytest.mark.parametrize("fail", [False, True])
def test_author_intent_never_falls_back_to_biographies(fail):
    client = Catalog(fail=fail)
    outcome = asyncio.run(service(client).search("лев толстой"))
    assert outcome.books == []
    assert client.book_queries == []
    assert outcome.source_unavailable is fail


def test_full_name_excludes_other_author_with_same_surname_and_shows_works():
    client = Catalog(
        [
            AuthorResult("1", "Лев Николаевич Толстой"),
            AuthorResult("2", "Алексей Николаевич Толстой"),
        ]
    )
    outcome = asyncio.run(service(client).search("лев толстой"))
    assert [author.author_id for author in outcome.authors] == ["1"]
    assert {book.book_id for book in outcome.books} == {"200", "202"}
    assert client.expanded == ["1"] and client.book_queries == []
    assert not outcome.approximate


def test_ambiguous_surname_requires_choice_without_loading_any_catalog():
    client = Catalog(
        [AuthorResult("1", "Лев Толстой"), AuthorResult("2", "Алексей Толстой")]
    )
    outcome = asyncio.run(service(client).search("Толстой"))
    assert outcome.plan.mode == SearchMode.AUTHOR
    assert len(outcome.authors) == 2 and not outcome.books
    assert not client.expanded and not client.book_queries


@pytest.mark.parametrize(
    "query,expected",
    [
        ("книги Льва Толстого", "Лев Толстой"),
        ("произведения Фёдора Достоевского", "Фёдор Достоевский"),
        ("книги Толстого", "толстой"),
        ("агата кристи", "Агата Кристи"),
        ("джейн остин", "Джейн Остин"),
        ("Стивен Кинг", "Стивен Кинг"),
    ],
)
def test_author_queries_are_recognized_and_normalized(query, expected):
    plan = build_search_plan(query)
    assert plan.mode == SearchMode.AUTHOR
    assert (plan.author or plan.cleaned_query).casefold().replace(
        "ё", "е"
    ) == expected.casefold().replace("ё", "е")


def test_typo_proposes_correction_without_loading_wrong_books():
    client = Catalog()
    outcome = asyncio.run(service(client).search("лев толтсой"))
    assert outcome.suggested_query == "Лев Толстой"
    assert not outcome.books and not client.expanded and not client.book_queries


def test_matching_full_names_requires_given_name_but_allows_patronymic_and_order():
    assert not author_name_matches("Лев Толстой", "Алексей Толстой")
    assert author_name_matches("Лев Толстой", "Толстой Лев Николаевич")
    assert author_name_matches("Толстой", "Алексей Толстой")


def test_author_title_does_not_accept_namesake_author():
    class Client(Catalog):
        async def search(self, query, limit=40):
            return [SearchResult("300", "Исповедь", "Алексей Толстой")]

    assert not asyncio.run(
        service(Client()).search_author_title("Лев Толстой", "Исповедь")
    ).books


def test_author_page_failure_keeps_author_without_biography_fallback():
    class Client(Catalog):
        async def author_books(self, author_id, limit):
            raise TimeoutError

    client = Client([AuthorResult("1", "Лев Толстой")])
    outcome = asyncio.run(service(client).search("лев толстой"))
    assert outcome.source_unavailable and outcome.authors and not outcome.books
    assert not client.book_queries


def test_mismatched_author_page_identity_is_rejected():
    class Client(Catalog):
        async def author_books(self, author_id, limit):
            return "Алексей Толстой", [
                SearchResult("1", "Пётр Первый", "Алексей Толстой")
            ]

    outcome = asyncio.run(
        service(Client([AuthorResult("1", "Лев Толстой")])).search("лев толстой")
    )
    assert not outcome.books and outcome.source_unavailable


def test_conflicting_patronymics_are_not_interchangeable():
    assert not author_name_matches("Лев Николаевич Толстой", "Лев Львович Толстой")
    assert author_name_matches("Лев Николаевич Толстой", "Лев Толстой")


def test_quoted_name_still_searches_literal_book_title():
    client = Catalog()
    outcome = asyncio.run(service(client).search("«Лев Толстой»"))
    assert outcome.plan.mode == SearchMode.EXACT
    assert outcome.books and not client.author_queries


def test_single_patronymic_like_surname_is_not_a_wildcard():
    assert not author_name_matches("Ильич", "Незнакомый автор")


def test_explicit_patronymic_survives_query_normalization():
    client = Catalog(
        [
            AuthorResult("1", "Лев Николаевич Толстой"),
            AuthorResult("2", "Лев Львович Толстой"),
        ]
    )
    outcome = asyncio.run(service(client).search("лев николаевич толстой"))
    assert [item.author_id for item in outcome.authors] == ["1"]
    assert client.expanded == ["1"]


@pytest.mark.parametrize('query',['Леа Толстнй','Толстнй Леа','Джейи Остни'])
def test_two_name_typos_require_confirmation_without_fragment_search(query):
    class NoisyCatalog(Catalog):
        async def search(self,query,limit=40):
            self.book_queries.append(query)
            return [SearchResult('900','Ор леа н','Ли Стеффи')] if query.casefold()=='леа' else []
    client=NoisyCatalog()
    outcome=asyncio.run(service(client).search(query))
    assert outcome.plan.mode==SearchMode.AUTHOR
    assert not outcome.books and not outcome.authors
    assert outcome.suggested_query==('Джейн Остин' if 'Остни' in query else 'Лев Толстой')
    assert client.book_queries==[] and client.author_queries==[] and client.expanded==[]


def test_two_typo_suggestion_does_not_intercept_literal_titles_or_single_word():
    from app.services.author_names import suggest_author_query
    assert suggest_author_query('Леа') is None
    assert suggest_author_query('Книга жизни') is None
    class LiteralCatalog(Catalog):
        async def search(self,query,limit=40):
            self.book_queries.append(query)
            return [SearchResult('901','Леа Толстнй','Другой автор')]
    client=LiteralCatalog()
    result=asyncio.run(service(client).search('«Леа Толстнй»'))
    assert result.plan.mode==SearchMode.EXACT
    assert result.books[0].book_id=='901' and not result.suggested_query


def test_two_word_correction_remains_bounded_and_ambiguous_names_are_not_guessed():
    from app.services.author_names import suggest_author_query
    assert suggest_author_query('Леа Талстнй') is None
    assert suggest_author_query('Леа Толстнй',['Лев Толстай']) is None
