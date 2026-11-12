"""Generated coverage across catalog names, not aliases for reported failures.

Synthetic metadata/IDs deliberately avoid the curated author-name list.
The fake source only performs substring retrieval; production does the matching.
"""
import asyncio
import pytest

from app.flibusta import AuthorResult, SearchResult
from app.services.search import SearchService, SearchMode
from app.services.search_logic import norm

NAMES = ['Джонатан Франзен', 'Кадзуо Исигуро', 'Мариам Петросян',
         'Милорад Павич', 'Терентий Ясногорский', 'Валерий Соколин',
         'Светлана Алексиевич', 'Хорхе Борхес', 'Вера Полозкова',
         'Марина Степнова', 'Гузель Яхина', 'Евгений Водолазкин']


def variants(name):
    first, last = name.split()
    return [name, name.lower(), last + ' ' + first, '  ' + first + '   ' + last + ' ',
            first[:2] + first[3:] + ' ' + last,
            first + ' ' + last[:2] + last[3] + last[2] + last[4:]]


class Catalog:
    def __init__(self, name):
        self.name = name
        self.calls = []

    async def search(self, query, limit=40):
        self.calls.append('book')
        # Upstream can return irrelevant results for every variant.
        return [SearchResult('noise', 'Лес и тени', 'Другая фамилия')]

    async def search_authors(self, query, limit=20):
        self.calls.append('author')
        return [AuthorResult('123', self.name)] if norm(query) in norm(self.name) else []

    async def author_books(self, author_id, limit=40):
        assert author_id == '123'
        self.calls.append('works')
        return self.name, [SearchResult('456', 'Карта туманных островов', self.name)]


@pytest.mark.parametrize('name,query', [(name, q) for name in NAMES for q in variants(name)])
def test_generated_name_variants_return_only_validated_author_works(name, query):
    catalog = Catalog(name)
    result = asyncio.run(SearchService(catalog, book_limit=40, author_limit=20).search(query))
    assert [b.book_id for b in result.books] == ['456']
    assert [a.author_id for a in result.authors] == ['123']
    assert result.plan.mode == SearchMode.AUTHOR
    assert catalog.calls.count('author') <= 4
    assert catalog.calls.count('book') <= 4


@pytest.mark.parametrize('query', ['Алла Филина', 'Алла Неизвестная', 'Валерий Франзен',
                                    'Марина Исигуро', 'Тайна жизни', 'Четыре тысячи недель'])
def test_full_query_is_not_satisfied_by_one_word_or_another_person(query):
    class Noisy(Catalog):
        async def search(self, query, limit=40):
            return [SearchResult('noise', 'Алладин, мудрец и джин', 'Галина Емельянова'),
                    SearchResult('other', 'Жизнь', 'Иван Петров')]
        async def search_authors(self, query, limit=20):
            return []
    result = asyncio.run(SearchService(Noisy(''), book_limit=40, author_limit=20).search(query))
    assert not result.books and not result.authors


def test_two_near_tied_catalog_authors_require_choice_not_silent_selection():
    class Ambiguous(Catalog):
        async def search_authors(self, query, limit=20):
            return [AuthorResult('1', 'Лев Толстой'), AuthorResult('2', 'Лев Толстай')]
    result = asyncio.run(SearchService(Ambiguous(''), book_limit=40, author_limit=20).search('Леа Толстнй'))
    assert not result.books
    assert result.query_choices or len(result.authors) > 1 or result.suggested_query
