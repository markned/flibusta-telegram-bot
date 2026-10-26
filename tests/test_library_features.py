import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.flibusta import AuthorResult, SearchResult
from app.repositories.access import AccessRepository
from app.repositories.cache import CacheRepository
from app.repositories.db import Database
from app.repositories.download_history import DownloadHistoryRepository
from app.repositories.download_history import DownloadHistoryItem
from app.repositories.favorites import FavoritesRepository
from app.repositories.last_books import LastBooksRepository
from app.services.cached_flibusta import CachedFlibustaClient
from app.services.intent_router import IntentKind, route_intent
from app.services.query_analyzer import analyze_query
from app.services.search import SearchMode, SearchOutcome
from app.services.search.types import SearchPlan


def run(coro):
    return asyncio.run(coro)


def test_query_analysis():
    analysis = analyze_query('"Мастер и Маргарита" epub')
    assert analysis.quoted_title
    assert analysis.cleaned == '"Мастер и Маргарита"'
    assert analysis.format_hint == "epub"
    assert analyze_query("Лев Толстой").likely_author
    separated = analyze_query("Лев Толстой - Война и мир")
    assert separated.author_part == "Лев Толстой"
    assert separated.title_part == "Война и мир"


def test_cache_hit_stale_and_cleanup(tmp_path: Path):
    db = Database(str(tmp_path / "db.sqlite"))
    run(db.initialize())
    repo = CacheRepository(db)
    run(repo.set("x", "book_search", [{"book_id": "1"}], 60))
    assert run(repo.get("x")) == [{"book_id": "1"}]
    run(repo.set("old", "book_search", [{"book_id": "2"}], -1))
    assert run(repo.get("old")) is None
    assert run(repo.get_stale("old", 60)) == [{"book_id": "2"}]
    assert run(repo.clear()) == 1


def test_favorites_history_and_last_book(tmp_path: Path):
    db = Database(str(tmp_path / "db.sqlite"))
    run(db.initialize())
    favorites = FavoritesRepository(db)
    history = DownloadHistoryRepository(db)
    last = LastBooksRepository(db)
    run(favorites.add(1, "7", "Book", "Author"))
    run(favorites.add(1, "7", "Book", "Author"))
    assert run(favorites.count(1)) == 1
    run(history.add(user_id=1, book_id="7", title="Book", author="Author", format="epub", filename="b.epub", file_size_bytes=4, delivery_target="telegram", status="sent"))
    assert run(history.recent(1))[0].title == "Book"
    run(last.upsert(1, "7", "Book", "Author", "opened"))
    assert run(last.get(1)).book_id == "7"


def test_access_invite_and_management(tmp_path: Path):
    db = Database(str(tmp_path / "db.sqlite"))
    run(db.initialize())
    repo = AccessRepository(db)
    run(repo.request_access(1, "u", "User"))
    run(repo.set_status(1, "approved", 99))
    code = run(repo.create_invite(99, 1))
    assert run(repo.redeem_invite(code, 2, "v", "Visitor")) is True
    assert run(repo.redeem_invite(code, 3, "w", "Other")) is False
    run(repo.ensure_user(7, "blocked", 1))
    assert run(repo.delete_user(7)) == 1


class CountingFlibusta:
    def __init__(self):
        self.calls = 0
        self.fail = False

    async def search(self, query, limit=8):
        self.calls += 1
        if self.fail:
            raise RuntimeError("source unavailable")
        return [SearchResult("1", "Book", "Author")]

    async def close(self):
        pass


def test_cached_client_uses_fresh_cache(tmp_path: Path):
    db = Database(str(tmp_path / "db.sqlite"))
    run(db.initialize())
    raw = CountingFlibusta()
    cached = CachedFlibustaClient(raw, CacheRepository(db), enabled=True, ttls={"book_search": 60})
    assert run(cached.search("Book"))[0].title == "Book"
    assert run(cached.search("book"))[0].title == "Book"
    assert raw.calls == 1


def test_cached_client_uses_stale_cache_on_source_error(tmp_path: Path):
    db = Database(str(tmp_path / "db.sqlite"))
    run(db.initialize())
    raw = CountingFlibusta()
    repo = CacheRepository(db)
    cached = CachedFlibustaClient(raw, repo, enabled=True, ttls={"book_search": -1}, stale_if_error_seconds=60)
    assert run(cached.search("Book"))[0].title == "Book"


def test_cached_client_uses_stale_cache_on_source_timeout(tmp_path: Path):
    class SlowFlibusta(CountingFlibusta):
        async def search(self, query, limit=8):
            self.calls += 1
            if self.calls > 1:
                await asyncio.sleep(0.1)
            return [SearchResult("1", "Book", "Author")]

    db = Database(str(tmp_path / "db.sqlite"))
    run(db.initialize())
    raw = SlowFlibusta()
    repo = CacheRepository(db)
    cached = CachedFlibustaClient(
        raw,
        repo,
        enabled=True,
        ttls={"book_search": -1},
        stale_if_error_seconds=60,
        source_timeout_seconds=0.01,
    )
    assert run(cached.search("Book"))[0].title == "Book"
    assert run(cached.search("Book"))[0].title == "Book"
    assert raw.calls == 2
    raw.fail = True
    assert run(cached.search("Book"))[0].title == "Book"


def test_cached_client_does_not_persist_empty_search(tmp_path: Path):
    class EmptyFlibusta:
        def __init__(self):
            self.calls = 0

        async def search(self, query, limit=8):
            self.calls += 1
            return []

        async def close(self):
            pass

    db = Database(str(tmp_path / "db.sqlite"))
    run(db.initialize())
    raw = EmptyFlibusta()
    cached = CachedFlibustaClient(raw, CacheRepository(db), enabled=True, ttls={"book_search": 60})
    assert run(cached.search("Missing")) == []
    assert run(cached.search("missing")) == []
    assert raw.calls == 2


def test_cached_client_coalesces_identical_concurrent_requests(tmp_path: Path):
    class SlowFlibusta(CountingFlibusta):
        async def search(self, query, limit=8):
            self.calls += 1
            await asyncio.sleep(0.02)
            return [SearchResult("1", "Book", "Author")]

    async def scenario():
        db = Database(str(tmp_path / "db.sqlite"))
        await db.initialize()
        raw = SlowFlibusta()
        cached = CachedFlibustaClient(
            raw,
            CacheRepository(db),
            enabled=True,
            ttls={"book_search": 60},
        )
        first, second = await asyncio.gather(
            cached.search("Book"),
            cached.search("book"),
        )
        return raw.calls, first, second, len(cached._inflight)

    calls, first, second, inflight = run(scenario())
    assert calls == 1
    assert first == second
    assert inflight == 0


def test_database_uses_low_memory_wal_pragmas(tmp_path: Path):
    async def scenario():
        db = Database(str(tmp_path / "db.sqlite"))
        await db.initialize()
        async with db.connect() as conn:
            journal = (await (await conn.execute("PRAGMA journal_mode")).fetchone())[0]
            synchronous = (await (await conn.execute("PRAGMA synchronous")).fetchone())[0]
            cache_size = (await (await conn.execute("PRAGMA cache_size")).fetchone())[0]
            checkpoint = (await (await conn.execute("PRAGMA wal_autocheckpoint")).fetchone())[0]
        return journal, synchronous, cache_size, checkpoint

    journal, synchronous, cache_size, checkpoint = run(scenario())
    assert journal == "wal"
    assert synchronous == 1  # NORMAL
    assert cache_size == -2048
    assert checkpoint == 500


def test_intent_router_deterministic_examples():
    assert route_intent("Дюна").kind == IntentKind.EXACT_SEARCH
    assert route_intent("Эдит Патту").kind == IntentKind.AUTHOR_SEARCH
    for query in (
        "Исповедь Толстой",
        "исповедь толстой",
        "Толстой Исповедь",
        "идиот достоевский",
        "преступление и наказание достоевский",
        "Восток Патту",
    ):
        assert route_intent(query).kind == IntentKind.AUTHOR_TITLE_SEARCH
    assert route_intent("антиутопия").kind == IntentKind.UNSUPPORTED_TOPIC
    assert route_intent('"Антиутопия"').kind == IntentKind.EXACT_SEARCH
    assert route_intent("Подборка стихотворений").kind == IntentKind.EXACT_SEARCH
    separated = route_intent("Толстой — Исповедь")
    assert separated.kind == IntentKind.AUTHOR_TITLE_SEARCH
    assert separated.author_part == "Толстой" and separated.title_part == "Исповедь"


class _FakeUser:
    def __init__(self, user_id=501):
        self.id = user_id
        self.username = "u"
        self.full_name = "User"


class _FakeChat:
    id = 777


class _FakeBot:
    async def send_chat_action(self, *args, **kwargs):
        return None


class _FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.from_user = _FakeUser()
        self.chat = _FakeChat()
        self.bot = _FakeBot()
        self.answers = []
        self.photos = []
        self.edits = []
        self.edit_payloads = []

    async def answer(self, text, *args, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def answer_photo(self, photo, *args, **kwargs):
        self.photos.append((photo, kwargs))
        return self

    async def edit_text(self, text, *args, **kwargs):
        self.edits.append(text)
        self.edit_payloads.append((text, kwargs))
        return self

    async def delete(self):
        return None


class _FakeCallback:
    def __init__(self, data="book:1", user_id=501):
        self.data = data
        self.from_user = _FakeUser(user_id)
        self.message = _FakeMessage()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


def _outcome(query, *, mode=SearchMode.EXACT, books=None, authors=None):
    plan = SearchPlan(query, query, mode, (query,), ())
    return SearchOutcome(plan, books or [], authors or [], (query,))


def test_send_search_results_sends_message(monkeypatch):
    import app.main as main

    class Service:
        async def search_books(self, query):
            return _outcome(query, books=[SearchResult("1", "Мастер и Маргарита", "Михаил Булгаков")])

    monkeypatch.setattr(main, "search_service", Service())
    main.search_timestamps.clear()
    message = _FakeMessage()
    run(main.send_search_results(message, "мастер и маргарита"))
    assert any("Нашёл книги" in text for text, _ in message.answers + message.edit_payloads)
    progress_markup = message.answers[0][1]["reply_markup"]
    progress_labels = [button.text for row in progress_markup.keyboard for button in row]
    assert "📱 Мои устройства" in progress_labels and "⚙️ Kindle" not in progress_labels


def test_text_routing_uses_single_search_entrypoint(monkeypatch):
    import app.main as main

    calls = []

    async def smart(message, query, **kwargs):
        calls.append(query)
        return True

    monkeypatch.setattr(main, "send_smart_results", smart)
    run(main.search_text(_FakeMessage("Эдит Патту")))
    run(main.search_text(_FakeMessage("исповедь толстой")))
    assert calls == ["Эдит Патту", "исповедь толстой"]


def test_broad_topic_is_not_sent_as_literal_catalog_search(monkeypatch):
    import app.main as main

    async def forbidden(*args, **kwargs):
        raise AssertionError("broad topic must not become a literal title search")

    monkeypatch.setattr(main, "send_smart_results", forbidden)
    message = _FakeMessage("антиутопия")
    run(main.search_text(message))
    assert "по названию и автору" in message.answers[-1][0]


def test_reader_button_is_not_silent():
    import app.main as main

    message = _FakeMessage("📱 Читалки")
    run(main.search_text(message))
    assert message.answers


def test_admin_intent_is_admin_only_and_dry_run(monkeypatch):
    import app.main as main

    class Command:
        args = "исповедь толстой"

    monkeypatch.setattr(main.settings, "admin_user_ids", "501")
    message = _FakeMessage()
    run(main.admin_intent(message, Command()))
    assert "author_title_search" in message.answers[-1][0]
    assert "handler: author_title_search" in message.answers[-1][0]


def test_book_card_callback_opens_book(monkeypatch):
    import app.main as main
    from app.flibusta import BookDetails

    class Flibusta:
        async def details(self, book_id):
            return BookDetails(book_id, "Book", ["Author"], [], [], [], [], None, None, "Ann", [], "x")

    class Resolver:
        async def resolve(self, **kwargs):
            return None

    async def preferred(*args):
        return "epub"

    async def no_op(*args, **kwargs):
        return None

    async def not_favorite(*args, **kwargs):
        return False

    monkeypatch.setattr(main, "flibusta", Flibusta())
    monkeypatch.setattr(main, "cover_resolver", Resolver())
    monkeypatch.setattr(main, "_preferred_format", preferred)
    monkeypatch.setattr(main.last_books_repo, "upsert", no_op)
    monkeypatch.setattr(main.favorites_repo, "exists", not_favorite)
    callback = _FakeCallback()
    run(main.show_book(callback))
    assert any("Book" in text for text, _ in callback.message.answers)


def test_default_command_menu_is_hidden_and_admin_is_scoped(monkeypatch):
    import app.main as main

    class Bot:
        def __init__(self):
            self.calls = []

        async def set_my_commands(self, commands, **kwargs):
            self.calls.append((commands, kwargs))

    bot = Bot()
    monkeypatch.setattr(main.settings, "ui_hide_command_menu_for_users", True)
    monkeypatch.setattr(main.settings, "ui_show_power_user_commands", False)
    monkeypatch.setattr(main.settings, "ui_show_admin_commands", True)
    monkeypatch.setattr(main.settings, "admin_user_ids", "9")
    run(main.setup_bot_commands(bot))
    assert bot.calls[0][0] == []
    assert type(bot.calls[0][1]["scope"]).__name__ == "BotCommandScopeDefault"
    assert type(bot.calls[1][1]["scope"]).__name__ == "BotCommandScopeChat"


def test_power_user_commands_have_no_ai_or_discovery():
    import app.main as main

    commands = {item.command for item in main._power_user_bot_commands()}
    assert not commands & {"recommend", "discover", "discover_web"}
    assert {"start", "search", "author"} <= commands


def test_start_help_and_home_describe_deterministic_search(monkeypatch):
    import app.main as main
    from app.ui.home import home_text, search_help_text

    class Command:
        args = ""

    monkeypatch.setattr(main.settings, "access_control_enabled", False)
    message = _FakeMessage()
    run(main.start(message, Command()))
    assert any("Полка" in text for text, _ in message.answers)
    text = home_text() + search_help_text()
    assert "подборк" not in text.lower()
    assert "антиутопия" not in text.lower()


def test_home_uses_one_persistent_menu_without_inline_duplicates(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "ui_reply_keyboard_enabled", True)
    message = _FakeMessage()
    run(main.send_home(message))
    assert len(message.answers) == 1
    markup = message.answers[0][1]["reply_markup"]
    labels = [button.text for row in markup.keyboard for button in row]
    assert labels == ["⭐ Избранное", "🕘 История", "📚 Последняя", "📱 Мои устройства", "🔥 Популярное", "📖 Жанры", "❓ Помощь"]
    assert not hasattr(markup, "inline_keyboard")


def test_help_only_has_contextual_back_button():
    from app.ui.home import help_keyboard

    labels = [button.text for row in help_keyboard().inline_keyboard for button in row]
    assert labels == ["🏠 В меню"]


def _history_item(
    item_id: int,
    book_id: str,
    title: str,
    *,
    target: str = "telegram",
    status: str = "sent",
    error: str | None = None,
) -> DownloadHistoryItem:
    return DownloadHistoryItem(
        id=item_id,
        user_id=501,
        book_id=book_id,
        title=title,
        author="Автор",
        format="epub",
        filename=f"{book_id}.epub",
        file_size_bytes=1024,
        delivery_target=target,
        status=status,
        created_at="2026-08-04T10:30:00+00:00",
        error=error,
    )


def test_history_is_friendly_and_books_open_from_buttons():
    from app.ui.library import history_keyboard, history_text

    items = [
        _history_item(1, "10", "Первая книга"),
        _history_item(2, "20", "Вторая книга", target="kindle"),
    ]
    text = history_text(items, now=datetime(2026, 8, 4, 12, tzinfo=UTC))
    assert "История книг" in text
    assert "Сегодня, 10:30" in text
    assert "в Telegram" in text and "в Kindle" in text
    markup = history_keyboard(items)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "book:10" in callbacks and "book:20" in callbacks
    assert "history_failed" in callbacks and "home" in callbacks


def test_failed_history_hides_raw_technical_error():
    from app.ui.library import history_text

    item = _history_item(
        1,
        "10",
        "Книга",
        status="failed",
        error="SMTPAuthenticationError password=never-show-this",
    )
    text = history_text([item], failed=True)
    assert "сервис отправки временно недоступен" in text
    assert "never-show-this" not in text


def _book_details_for_card(annotation="Short", cover_url=None):
    from app.flibusta import BookDetails, DownloadFormat

    return BookDetails(
        book_id="1",
        title="Book",
        authors=["Author"],
        author_refs=[],
        translators=[],
        illustrators=[],
        genres=["Жанр"],
        file_size="1M",
        pages=10,
        annotation=annotation,
        formats=[DownloadFormat("epub", "EPUB", "url")],
        page_url="x",
        cover_url=cover_url,
    )


def test_book_card_sends_photo_when_cover_available(monkeypatch):
    import app.main as main
    from app.services.covers.types import BookCover, CoverImage

    class Resolver:
        async def resolve(self, **kwargs):
            return BookCover("https://example.com/cover.jpg", "fake", 400, 600, 0.9)

    async def download(*args, **kwargs):
        return CoverImage(b"img", "image/jpeg", 400, 600, "cover.jpg", "https://example.com/cover.jpg")

    monkeypatch.setattr(main, "cover_resolver", Resolver())
    monkeypatch.setattr(main, "download_cover", download)
    message = _FakeMessage()
    run(main.send_book_card(message, _book_details_for_card(cover_url="https://example.com/cover.jpg"), preferred_format="epub", is_favorite=False))
    assert message.photos and message.photos[0][1]["reply_markup"] is not None


def test_book_card_without_cover_sends_text(monkeypatch):
    import app.main as main

    class Resolver:
        async def resolve(self, **kwargs):
            return None

    monkeypatch.setattr(main, "cover_resolver", Resolver())
    message = _FakeMessage()
    run(main.send_book_card(message, _book_details_for_card(), preferred_format="epub", is_favorite=False))
    assert message.answers and not message.photos


def test_book_card_photo_failure_falls_back_to_text(monkeypatch):
    import app.main as main
    from app.services.covers.types import BookCover, CoverImage

    class Resolver:
        async def resolve(self, **kwargs):
            return BookCover("https://example.com/cover.jpg", "fake", 400, 600, 0.9)

    async def download(*args, **kwargs):
        return CoverImage(b"img", "image/jpeg", 400, 600, "cover.jpg", "https://example.com/cover.jpg")

    class BadPhotoMessage(_FakeMessage):
        async def answer_photo(self, *args, **kwargs):
            raise RuntimeError("telegram failed")

    monkeypatch.setattr(main, "cover_resolver", Resolver())
    monkeypatch.setattr(main, "download_cover", download)
    message = BadPhotoMessage()
    run(main.send_book_card(message, _book_details_for_card(cover_url="https://example.com/cover.jpg"), preferred_format="epub", is_favorite=False))
    assert message.answers


def test_search_returns_dated_stale_cache_before_slow_refresh(tmp_path):
    from app.services.search import SearchService

    class SlowCatalog:
        async def search(self, *args, **kwargs):
            await asyncio.Event().wait()

        async def close(self):
            pass

    async def scenario():
        db = Database(str(tmp_path / "stale.db"))
        await db.initialize()
        repo = CacheRepository(db)
        await repo.set("book_search:дюна:10", "book_search", [SearchResult("10", "Дюна", "Фрэнк Герберт")], -1)
        cached = CachedFlibustaClient(SlowCatalog(), repo, enabled=True, ttls={"book_search": 60},
                                      source_timeout_seconds=10, stale_refresh_timeout_seconds=0.05)
        try:
            service = SearchService(cached, book_limit=10, author_limit=10, timeout_seconds=0.5)
            result = await asyncio.wait_for(service.search("Дюна"), timeout=0.3)
            assert result.books[0].book_id == "10"
            assert result.stale_at is not None
            assert result.source_unavailable is True
        finally:
            await cached.close()

    run(scenario())


def test_telegram_search_marks_approximate_stale_results(monkeypatch):
    from dataclasses import replace
    import app.main as main

    class Service:
        async def search(self, query):
            return replace(_outcome(query, mode=SearchMode.AUTHOR_TITLE,
                                    books=[SearchResult("10", "Дюны", "Фрэнк Герберт")]),
                           approximate=True, source_unavailable=True,
                           stale_at="2026-01-02T03:04:00+00:00")

    monkeypatch.setattr(main, "search_service", Service())
    main.search_timestamps.clear()
    message = _FakeMessage()
    run(main.send_smart_results(message, "Дюна Герберт"))
    text = " ".join(text for text, _ in message.answers + message.edit_payloads)
    assert "Результаты из кэша" in text and "02.01.2026" in text
    assert "Приблизительные совпадения" in text
    assert "<b>Нашёл книгу</b>" not in text


def test_coalesced_stale_provenance_is_request_local(tmp_path):
    from app.services.search import SearchService

    class Catalog:
        def __init__(self):
            self.calls = 0

        async def search(self, query, limit=10):
            self.calls += 1
            if query == "Дюна":
                await asyncio.Event().wait()
            return [SearchResult("11", "Идиот", "Федор Достоевский")]

        async def close(self):
            pass

    async def scenario():
        db = Database(str(tmp_path / "shared.db"))
        await db.initialize()
        repo = CacheRepository(db)
        await repo.set("book_search:дюна:10", "book_search", [SearchResult("10", "Дюна", "Фрэнк Герберт")], -1)
        raw = Catalog()
        cached = CachedFlibustaClient(raw, repo, enabled=True, ttls={"book_search": 60},
                                      stale_refresh_timeout_seconds=0.05)
        service = SearchService(cached, book_limit=10, author_limit=10)
        try:
            first, second, fresh = await asyncio.gather(service.search("Дюна"), service.search("Дюна"), service.search("Идиот"))
            assert first.stale_at == second.stale_at and first.stale_at is not None
            assert fresh.stale_at is None and fresh.source_unavailable is False
            assert raw.calls == 2
        finally:
            await cached.close()
    run(scenario())


def test_telegram_author_query_shows_works_and_ambiguous_names_require_choice(monkeypatch):
    import app.main as main
    from app.services.search import SearchService

    class Catalog:
        async def search_authors(self, query, limit):
            return [AuthorResult("1", "Лев Толстой"), AuthorResult("2", "Алексей Толстой")]

        async def author_books(self, author_id, limit):
            return "Лев Толстой", [SearchResult("200", "Война и мир", "Лев Толстой")]

    monkeypatch.setattr(main, "search_service", SearchService(Catalog(), book_limit=40, author_limit=20))
    main.search_timestamps.clear()
    message = _FakeMessage()
    run(main.send_smart_results(message, "лев толстой"))
    text = " ".join(text for text, _ in message.answers + message.edit_payloads)
    assert "Книги автора" in text
    buttons = [b for row in message.edit_payloads[-1][1]["reply_markup"].inline_keyboard for b in row]
    assert any(b.callback_data == "book:200" for b in buttons)
    ambiguous = _FakeMessage()
    run(main.send_smart_results(ambiguous, "Толстой"))
    assert "Уточни автора" in " ".join(text for text, _ in ambiguous.answers + ambiguous.edit_payloads)


def test_telegram_author_typo_requires_user_confirmation(monkeypatch):
    import app.main as main
    from app.services.search import SearchService

    class Catalog:
        async def search_authors(self, query, limit):
            return []

    monkeypatch.setattr(main, "search_service", SearchService(Catalog(), book_limit=40, author_limit=20))
    main.search_timestamps.clear()
    message = _FakeMessage()
    run(main.send_smart_results(message, "лев толтсой"))
    text = " ".join(text for text, _ in message.answers + message.edit_payloads)
    assert "Вы имели в виду" in text and "Лев Толстой" in text
    buttons = [b for row in message.edit_payloads[-1][1]["reply_markup"].inline_keyboard for b in row]
    assert any(b.callback_data.startswith("authorfix:") for b in buttons)


def test_author_confirmation_checks_owner_and_preserves_human_identity(monkeypatch):
    from datetime import UTC, datetime
    from aiogram.types import CallbackQuery, Chat, Message, User
    import app.main as main

    human = User(id=777, is_bot=False, first_name="Reader")
    bot = User(id=999, is_bot=True, first_name="Bot")
    msg = Message(message_id=10, date=datetime(2026, 1, 1, tzinfo=UTC),
                  chat=Chat(id=777, type="private"), from_user=bot)
    session = main._create_search_session(777, 777, "Лев Толстой", [])
    calls = []

    async def acknowledge(*args, **kwargs):
        pass

    async def search(message, query):
        calls.append((message.from_user.id, query))

    monkeypatch.setattr(main, "callback_answer", acknowledge)
    monkeypatch.setattr(main, "send_smart_results", search)
    try:
        wrong = CallbackQuery(id="bad", from_user=User(id=778, is_bot=False, first_name="Other"),
                              chat_instance="chat", message=msg, data=f"authorfix:{session.session_id}")
        run(main.confirm_author_search(wrong))
        assert not calls
        own = CallbackQuery(id="ok", from_user=human, chat_instance="chat", message=msg,
                            data=f"authorfix:{session.session_id}")
        run(main.confirm_author_search(own))
        assert calls == [(777, "Лев Толстой")]
    finally:
        main.search_sessions.pop(session.session_id, None)


def test_cancelled_catalog_probes_release_unshared_loader_but_keep_other_waiters(tmp_path):
    class Catalog:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.stopped = asyncio.Event()
            self.calls = 0

        async def search(self, query, limit=8):
            self.calls += 1
            self.started.set()
            try:
                await self.release.wait()
                return [SearchResult("10", "Book", "Author")]
            finally:
                self.stopped.set()

        async def close(self):
            pass

    async def scenario():
        db = Database(str(tmp_path / "probe.sqlite"))
        await db.initialize()
        raw = Catalog()
        cached = CachedFlibustaClient(raw, CacheRepository(db), enabled=False, ttls={"book_search": 60})
        try:
            first = asyncio.create_task(cached.search("Book"))
            second = asyncio.create_task(cached.search("Book"))
            await raw.started.wait()
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            assert not raw.stopped.is_set()
            raw.release.set()
            assert (await second)[0].book_id == "10" and raw.calls == 1
            raw.release.clear()
            raw.started.clear()
            raw.stopped.clear()
            only = asyncio.create_task(cached.search("Another"))
            await raw.started.wait()
            only.cancel()
            await asyncio.gather(only, return_exceptions=True)
            await asyncio.wait_for(raw.stopped.wait(), timeout=0.1)
            await asyncio.sleep(0)
            assert not cached._inflight
        finally:
            await cached.close()
    run(scenario())


def test_catalog_hints_read_only_bounded_fresh_public_metadata(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "hints.sqlite"))
        await db.initialize()
        repo = CacheRepository(db)
        await repo.set("books", "book_search", [SearchResult("100", "Имя розы", "Умберто Эко")], 60)
        await repo.set("authors", "author_search", [AuthorResult("10", "Умберто Эко")], 60)
        await repo.set("expired", "book_search", [SearchResult("101", "Expired", "Author")], -1)
        await repo.set("private", "unrelated", {"secret": "must not be decoded as catalog data"}, 60)
        await repo.set("huge", "book_search", [{"book_id": "bad", "title": "x" * 40000, "author": "A"}], 60)
        cached = CachedFlibustaClient(CountingFlibusta(), repo, enabled=True, ttls={})
        books, authors = await cached.catalog_hints()
        assert [b.book_id for b in books] == ["100"]
        assert [a.author_id for a in authors] == ["10"]
        assert cached.client.calls == 0
    run(scenario())


def test_telegram_generic_clarification_uses_owned_confirmation_sessions(monkeypatch):
    import app.main as main
    class Service:
        async def search(self, query):
            return SearchOutcome(SearchPlan(query, query, SearchMode.EXACT, (query,), ()), [], [], (),
                                 query_choices=('книги Умберто Эко', '«умберто эко»'), clarification='Это автор или название книги?')
    monkeypatch.setattr(main, 'search_service', Service())
    main.search_timestamps.clear()
    message = _FakeMessage()
    run(main.send_smart_results(message, 'умберто эко'))
    text, payload = message.edit_payloads[-1]
    assert 'Это автор или название' in text
    for row in payload['reply_markup'].inline_keyboard:
        button = row[0]
        session = main.search_sessions[button.callback_data.split(':')[1]]
        assert session.query == button.text
        assert session.user_id == message.from_user.id and session.chat_id == message.chat.id


def test_telegram_genre_pages_and_weekly_book_session(monkeypatch):
    import app.main as main
    class Catalog:
        async def genres(self):
            return [{'code': f'g{i}', 'name': f'Жанр {i}'} for i in range(35)]
        async def popular_books(self):
            return [SearchResult('640151', '1984', 'Джордж Оруэлл')]
    monkeypatch.setattr(main, 'flibusta', Catalog())
    main.search_timestamps.clear()
    message = _FakeMessage()
    run(main.send_catalog_browse(message, genres_only=True, page_number=1))
    buttons = [b for row in message.answers[-1][1]['reply_markup'].inline_keyboard for b in row]
    assert buttons[0].callback_data == 'browse:g16'
    assert buttons[-1].callback_data == 'browse:genres-2'
    assert len(buttons) == 18
    run(main.send_catalog_browse(message))
    text, payload = message.answers[-1]
    assert '7 дней' in text
    assert any(b.callback_data == 'book:640151' for row in payload['reply_markup'].inline_keyboard for b in row)
