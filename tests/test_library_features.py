import asyncio
from pathlib import Path

from app.flibusta import AuthorResult, SearchResult
from app.repositories.access import AccessRepository
from app.repositories.cache import CacheRepository
from app.repositories.db import Database
from app.repositories.download_history import DownloadHistoryRepository
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
    assert any("Библиотека им. Недзвецких" in text for text, _ in message.answers)
    text = home_text() + search_help_text()
    assert "подборк" not in text.lower()
    assert "антиутопия" not in text.lower()


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
