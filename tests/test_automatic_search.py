"""Agreed automatic search behavior; all IDs below are offline fixture IDs."""
import asyncio

from app.flibusta import AuthorResult, SearchResult
from app.services.search import SearchService, SearchMode


class Catalog:
    async def search(self, query, limit=40):
        return [SearchResult('noise', 'Алладин, мудрец и джин', 'Галина Емельянова')]

    async def search_authors(self, query, limit=20):
        await asyncio.sleep(.03)
        if query.casefold() in {'филина', 'фили', 'алла филина', 'филина алла'}:
            return [AuthorResult('alla', 'Филина Алла')]
        if query.casefold() in {'беркман', 'берк', 'оливер беркман'}:
            return [AuthorResult('oliver', 'Беркман Оливер')]
        return []

    async def author_books(self, author_id, limit=40):
        name, title = {'alla': ('Алла Филина', 'Выживет сильнейший'),
                       'oliver': ('Оливер Беркман', 'Четыре тысячи недель')}[author_id]
        return name, [SearchResult(author_id + '-book', title, name)]


def engine(client=None, timeout=1):
    return SearchService(client or Catalog(), book_limit=40, author_limit=20, timeout_seconds=timeout)


def test_author_with_slow_catalog_is_not_discarded_after_750ms():
    class Slow(Catalog):
        async def search_authors(self, query, limit=20):
            await asyncio.sleep(.8)
            return [AuthorResult('alla', 'Филина Алла')]
    result = asyncio.run(engine(Slow(), timeout=3).search('Алла Филина'))
    assert result.plan.mode == SearchMode.AUTHOR
    assert [b.book_id for b in result.books] == ['alla-book']


def test_unique_catalog_correction_returns_books_without_confirmation():
    result = asyncio.run(engine().search('Оливер Беркмн'))
    assert result.suggested_query == 'Беркман Оливер'
    assert [b.book_id for b in result.books] == ['oliver-book']
    assert not result.query_choices


def test_transliterated_author_returns_validated_works():
    result = asyncio.run(engine().search('Alla Filina'))
    assert [b.book_id for b in result.books] == ['alla-book']


def test_title_author_collision_returns_both_groups_without_question():
    class Collision(Catalog):
        async def search(self, query, limit=40):
            return [SearchResult('title', 'Алла Филина', 'Другой автор')]
    result = asyncio.run(engine(Collision()).search('Алла Филина'))
    assert [a.author_id for a in result.authors] == ['alla']
    assert [b.book_id for b in result.books] == ['title']
    assert not result.query_choices


def test_dropping_surname_never_admits_aladdin():
    class Empty(Catalog):
        async def search_authors(self, query, limit=20):
            return []
    assert not asyncio.run(engine(Empty()).search('Алла Филина')).books


def test_author_is_published_before_unrelated_slow_book_endpoint_finishes():
    class SlowBooks(Catalog):
        async def search(self, query, limit=40):
            await asyncio.Event().wait()
    async def scenario():
        updates = []
        async def progress(outcome):
            updates.append(outcome)
        result = await asyncio.wait_for(engine(SlowBooks(), timeout=1).search('Алла Филина', on_progress=progress), .8)
        assert result.books[0].book_id == 'alla-book'
        assert any(update.authors for update in updates)
        assert result.partial
    asyncio.run(scenario())


def test_first_title_results_are_published_while_author_probe_is_pending():
    class SlowAuthors(Catalog):
        async def search(self, query, limit=40):
            return [SearchResult('title', 'Карта туманных островов', 'Автор')]
        async def search_authors(self, query, limit=20):
            await asyncio.sleep(.06)
            return []
    async def scenario():
        seen = asyncio.Event()
        async def progress(outcome):
            if outcome.books:
                seen.set()
        task = asyncio.create_task(engine(SlowAuthors()).search('Карта туманных островов', on_progress=progress))
        await asyncio.wait_for(seen.wait(), .1)
        assert not task.done()
        result = await task
        assert result.books[0].book_id == 'title'
    asyncio.run(scenario())


def test_telegram_automatic_correction_has_books_and_no_confirmation(monkeypatch):
    import app.main as main
    from test_library_features import _FakeMessage
    monkeypatch.setattr(main, 'search_service', engine())
    main.search_timestamps.clear()
    message = _FakeMessage()
    asyncio.run(main.send_smart_results(message, 'Оливер Беркмн'))
    text, payload = message.edit_payloads[-1]
    assert 'Возможно, вы искали' in text and 'Беркман Оливер' in text
    buttons = [b for row in payload['reply_markup'].inline_keyboard for b in row]
    assert any('oliver-book' in b.callback_data for b in buttons)
    assert not any(b.callback_data.startswith('authorfix:') for b in buttons)


def test_telegram_collision_displays_both_types_without_mode_question(monkeypatch):
    import app.main as main
    from test_library_features import _FakeMessage
    class Collision(Catalog):
        async def search(self, query, limit=40):
            return [SearchResult('title', 'Алла Филина', 'Другой автор')]
    monkeypatch.setattr(main, 'search_service', engine(Collision()))
    main.search_timestamps.clear()
    message = _FakeMessage()
    asyncio.run(main.send_smart_results(message, 'Алла Филина'))
    text, payload = message.edit_payloads[-1]
    assert 'Есть автор с таким именем и книги с таким названием' in text
    assert 'Это автор или' not in text
    buttons = [b for row in payload['reply_markup'].inline_keyboard for b in row]
    assert any('title' in b.callback_data for b in buttons)
    assert any(b.callback_data.startswith('author:') for b in buttons)


def test_known_author_name_also_checks_for_a_literal_title():
    class Known(Catalog):
        async def search_authors(self, query, limit=20):
            return [AuthorResult('tolstoy', 'Лев Толстой')]
        async def search(self, query, limit=40):
            return [SearchResult('literal', 'Лев Толстой', 'Другой автор'),
                    SearchResult('biography', 'Лев Толстой. Биография', 'Другой автор')]
    result = asyncio.run(engine(Known()).search('Лев Толстой'))
    assert [b.book_id for b in result.books] == ['literal']
    assert [a.author_id for a in result.authors] == ['tolstoy']
