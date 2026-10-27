"""Generic catalog matching: generated errors, not query-specific aliases."""

import asyncio

import pytest

from app.flibusta import AuthorResult, SearchResult
from app.services.search import SearchService
from app.services.fuzzy_query import author_choices, edit_distance, full_query_supported

NAMES = [
    "Джонатан Франзен",
    "Кадзуо Исигуро",
    "Мариам Петросян",
    "Милорад Павич",
    "Терентий Ясногорский",
    "Валерий Соколин",
    "Светлана Алексиевич",
    "Хорхе Борхес",
]


def damage(name):
    first, last = name.casefold().split()
    return first[:2] + first[3:] + " " + last[:3] + last[4] + last[3] + last[5:-1] + "я"


@pytest.mark.parametrize("name", NAMES)
def test_generated_multi_error_names_are_resolved_from_catalog_candidates(name):
    choices = author_choices(damage(name), NAMES)
    assert choices == (name,)
    assert author_choices(" ".join(damage(name).split()[::-1]), NAMES) == (name,)


def test_distance_handles_missing_letters_replacements_and_transpositions():
    assert edit_distance("федр", "федор") == 1
    assert edit_distance("достоевисуий", "достоевский") == 2
    assert edit_distance("абвг", "авбг") == 1


def test_ambiguous_names_are_offered_not_silently_selected():
    choices = author_choices("Леа Толстнй", ["Лев Толстой", "Лев Толстай"])
    assert set(choices) == {"Лев Толстой", "Лев Толстай"}
    assert author_choices("Лев Толстой", ["Алексей Толстой"]) == ()


def test_full_query_guard_rejects_half_matches_and_keeps_title_author_combinations():
    assert not full_query_supported(
        "Федр Достоевисуий", "Ка федр а доверия", "Павел Степанов"
    )
    assert not full_query_supported("Леа Толстнй", "Ор леа н", "Ли Стеффи")
    assert not full_query_supported("Тайна жизни", "Жизнь", "Иван Петров")
    assert full_query_supported("Имя розы эко", "Имя розы", "Умберто Эко")
    assert full_query_supported("книга Дюна", "Дюна", "Фрэнк Герберт")


@pytest.mark.parametrize("query", ["Федр Достоевисуий", "Достоевисуий Федр"])
def test_screenshot_name_offers_confirmation_without_book_fragments(query):
    class Catalog:
        calls = []

        async def search(self, *args, **kwargs):
            self.calls.append("books")
            return [SearchResult("1", "Кафедра", "Другой автор")]

        async def search_authors(self, *args, **kwargs):
            self.calls.append("authors")
            return []

    client = Catalog()
    outcome = asyncio.run(
        SearchService(client, book_limit=40, author_limit=20).search(query)
    )
    assert outcome.suggested_query == "Фёдор Достоевский"
    assert not outcome.books and client.calls == []


def test_unknown_author_is_discovered_using_catalog_not_curated_names():
    class Catalog:
        calls = []

        async def search(self, query, limit=40):
            self.calls.append(("books", query))
            return [SearchResult("4", "Посторонняя книга", "Другой автор")]

        async def search_authors(self, query, limit=20):
            self.calls.append(("authors", query))
            return (
                [AuthorResult("50", "Джонатан Франзен")]
                if query in {"джон", "фран"}
                else []
            )

    client = Catalog()
    result = asyncio.run(
        SearchService(client, book_limit=40, author_limit=20).search("Джонатам Франзеи")
    )
    assert result.suggested_query == "Джонатан Франзен" and not result.books
    assert sum(kind == "authors" for kind, _ in client.calls) <= 4


def test_unresolved_two_word_query_never_accepts_noisy_single_word_results():
    class Catalog:
        async def search(self, query, limit=40):
            return [SearchResult("1", "Ка федр а доверия", "Другой автор")]

        async def search_authors(self, *args, **kwargs):
            return []

    result = asyncio.run(
        SearchService(Catalog(), book_limit=40, author_limit=20).search(
            "Федр Неизвстный"
        )
    )
    assert not result.books


def test_search_highlighting_never_splits_words_or_author_names():
    from app.flibusta import parse_search_results, parse_author_results

    html = '<div id="main"><li><a href="/b/1">Ка<strong>федр</strong>а доверия</a><a href="/a/2">Фё<b>дор</b> Достоевский</a></li></div>'
    assert parse_search_results(html)[0].title == "Кафедра доверия"
    assert parse_author_results(html)[0].name == "Фёдор Достоевский"


def test_catalog_learns_bounded_name_lexicon_and_uses_it_after_restart(tmp_path):
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository
    from app.services.cached_flibusta import CachedFlibustaClient

    async def scenario():
        db = Database(str(tmp_path / "names.sqlite"))
        await db.initialize()
        repo = CacheRepository(db)

        class Catalog:
            async def search_authors(self, query, limit=20):
                return [AuthorResult("50", "Джонатан Франзен")]

            async def close(self):
                pass

        client = CachedFlibustaClient(
            Catalog(), repo, enabled=True, ttls={"author_search": 3600}
        )
        await client.search_authors("Джонатан Франзен")
        await client.close()
        restored = CachedFlibustaClient(
            Catalog(), repo, enabled=True, ttls={"author_search": 3600}
        )
        assert "Джонатан Франзен" in await restored.author_name_hints()
        assert author_choices(
            "Джонатам Франзеи", await restored.author_name_hints()
        ) == ("Джонатан Франзен",)
        await restored.close()

    asyncio.run(scenario())


def test_title_evidence_is_not_lost_when_author_has_the_same_word():
    assert full_query_supported("Александр", "Александр", "Александр Иванов")
    assert full_query_supported("экоо имя розы", "Имя розы", "Умберто Эко")


def test_cold_catalog_prefix_probe_returns_a_candidate_before_deadline():
    class Catalog:
        requests = []

        async def search(self, *args, **kwargs):
            return []

        async def search_authors(self, query, limit=20):
            self.requests.append(query)
            await asyncio.sleep(0.05)
            return [AuthorResult("50", "Джонатан Франзен")] if query == "фран" else []

    async def scenario():
        client = Catalog()
        result = await SearchService(
            client, book_limit=40, author_limit=20, timeout_seconds=1.5
        ).search("Джонатам Франзеи")
        assert result.suggested_query == "Джонатан Франзен"
        assert client.requests == ["Джонатам Франзеи", "фран"]

    asyncio.run(scenario())


def test_ambiguous_surname_typo_asks_for_author_and_never_searches_books():
    class Catalog:
        async def search(self, *args, **kwargs):
            raise AssertionError("no book fallback")

        async def search_authors(self, *args, **kwargs):
            raise AssertionError("confirm first")

    result = asyncio.run(
        SearchService(Catalog(), book_limit=40, author_limit=20).search("Толстнй")
    )
    assert not result.books and not result.suggested_query
    assert set(result.query_choices) == {"книги Лев Толстой", "книги Алексей Толстой"}


@pytest.mark.parametrize("name", NAMES[:3] + NAMES[4:5])
def test_initial_and_misspelled_surname_use_the_same_general_matcher(name):
    first, last = name.split()
    query = first[0] + ". " + last[:2] + last[3] + last[2] + last[4:]
    assert author_choices(query, NAMES) == (name,)


def test_explicit_author_search_uses_the_same_confirmation_path():
    class Catalog:
        async def search_authors(self, *args, **kwargs):
            raise AssertionError("confirm first")

    result = asyncio.run(
        SearchService(Catalog(), book_limit=40, author_limit=20).search_author(
            "Федр Достоевисуий"
        )
    )
    assert result.suggested_query == "Фёдор Достоевский" and not result.authors


def test_exact_long_titles_are_not_rejected_by_fuzzy_work_budget():
    title = " ".join(f"слово{i}" for i in range(20))
    assert full_query_supported(title, title, "Автор")


def test_name_lexicon_is_bounded_and_never_learns_raw_user_queries(tmp_path):
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository
    from app.services.cached_flibusta import CachedFlibustaClient

    async def scenario():
        db = Database(str(tmp_path / "bounds.sqlite"))
        await db.initialize()
        repo = CacheRepository(db)
        names = [
            "Писатель "
            + chr(97 + i // 676)
            + chr(97 + (i // 26) % 26)
            + chr(97 + i % 26)
            for i in range(2100)
        ]

        class Catalog:
            async def search_authors(self, query, limit=20):
                return [AuthorResult(str(i), name) for i, name in enumerate(names)]

            async def close(self):
                pass

        client = CachedFlibustaClient(
            Catalog(), repo, enabled=True, ttls={"author_search": 3600}
        )
        await client.search_authors("личный текст запроса", limit=2100)
        hints = await client.author_name_hints()
        assert len(hints) == 2048 and "личный текст запроса" not in hints
        await client.close()
        assert len(await repo.get("author-name-lexicon:v1")) == 2048

    asyncio.run(scenario())


def test_case_yo_and_byline_order_do_not_create_false_ambiguity():
    assert author_choices("Семн Семёнов", ["Семён Семенов", "СЕМЕНОВ СЕМЕН"]) == (
        "Семён Семенов",
    )


def test_transient_lexicon_read_failure_does_not_overwrite_saved_names(tmp_path):
    from app.repositories.db import Database
    from app.repositories.cache import CacheRepository
    from app.services.cached_flibusta import CachedFlibustaClient

    async def scenario():
        db = Database(str(tmp_path / "read-failure.sqlite"))
        await db.initialize()
        base = CacheRepository(db)
        await base.set(
            "author-name-lexicon:v1", "author_lexicon", ["Джонатан Франзен"], 30 * 86400
        )

        class Flaky:
            failed = False

            async def get(self, key):
                if key == "author-name-lexicon:v1" and not self.failed:
                    self.failed = True
                    raise TimeoutError
                return await base.get(key)

            async def set(self, *args, **kwargs):
                return await base.set(*args, **kwargs)

            async def get_stale_entry(self, *args):
                return await base.get_stale_entry(*args)

        class Catalog:
            async def search_authors(self, *args, **kwargs):
                return [AuthorResult("99", "Кадзуо Исигуро")]

            async def close(self):
                pass

        client = CachedFlibustaClient(
            Catalog(), Flaky(), enabled=True, ttls={"author_search": 3600}
        )
        await client.search_authors("Исигуро")
        await client.close()
        assert "Джонатан Франзен" in await base.get("author-name-lexicon:v1")

    asyncio.run(scenario())


def test_initial_expansion_is_a_confirmation_not_an_exact_full_name_or_guess():
    assert author_choices("Ф. Достоевский", ["Фёдор Достоевский"]) == (
        "Фёдор Достоевский",
    )
    assert author_choices("Ф. Д.", ["Фёдор Достоевский"]) == ()
    assert author_choices("Фёдор Достоевский", ["Фёдор Достоевский"]) == ()
