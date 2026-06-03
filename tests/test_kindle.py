import asyncio
from pathlib import Path

import pytest

from app.flibusta import BookDetails, DownloadFormat
from app.handlers.kindle import format_history, user_message_for_exception
from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.services.conversion import ConversionService
from app.services.email_sender import EmailConfigurationError, EmailSender, mask_smtp_identity, validate_smtp_from_email
from app.services.kindle import (
    KindleFileTooLargeError,
    KindleRateLimitError,
    KindleService,
    KindleSettingsMissingError,
    choose_best_format,
    mask_email,
    sanitize_filename,
    validate_kindle_email,
)
from app.services.kindle_queue import KindleQueue


def run(coro):
    return asyncio.run(coro)


def test_validate_kindle_email_accepts_only_kindle_domains() -> None:
    assert validate_kindle_email("reader@kindle.com") == "reader@kindle.com"
    assert validate_kindle_email("reader@free.kindle.com") == "reader@free.kindle.com"
    with pytest.raises(Exception):
        validate_kindle_email("reader@gmail.com")


def test_validate_smtp_from_email_accepts_normal_email() -> None:
    assert validate_smtp_from_email("books@example.com") == "books@example.com"
    with pytest.raises(EmailConfigurationError):
        validate_smtp_from_email("not-an-email")


def test_mask_email_keeps_domain_visible() -> None:
    assert mask_email("mark@example.com") == "m***@example.com"
    assert mask_email("abc@kindle.com") == "a***@kindle.com"
    assert mask_email("a@kindle.com") == "a***@kindle.com"


def test_sanitize_filename() -> None:
    assert sanitize_filename("bad/../name\nbook.epub") == "bad .. name book.epub"
    assert sanitize_filename("") == "book"
    assert sanitize_filename("x" * 200 + ".epub").endswith(".epub")
    assert len(sanitize_filename("x" * 200 + ".epub")) <= 120


def test_choose_best_format_prefers_user_then_epub_then_fb2() -> None:
    formats = [
        DownloadFormat("fb2", "FB2", "fb2-url"),
        DownloadFormat("epub", "EPUB", "epub-url"),
        DownloadFormat("pdf", "PDF", "pdf-url"),
    ]
    assert choose_best_format(formats, "fb2").code == "fb2"
    assert choose_best_format(formats, "mobi").code == "epub"


class FakeFlibusta:
    def __init__(self, content: bytes = b"book"):
        self.content = content

    async def details(self, book_id: str) -> BookDetails:
        return BookDetails(
            book_id=book_id,
            title="Book",
            authors=["Author"],
            author_refs=[],
            translators=[],
            illustrators=[],
            genres=[],
            file_size=None,
            pages=None,
            annotation=None,
            formats=[DownloadFormat("epub", "EPUB", "epub-url")],
            page_url="page",
        )

    async def download(self, url: str, max_bytes: int):
        return self.content, "book.epub", "application/epub+zip"


class FakeEmailSender:
    async def send_attachment(self, **kwargs):
        return None


def build_service(tmp_path: Path, *, content: bytes = b"book", limit: int = 5):
    db = Database(str(tmp_path / "bot.db"))
    settings_repo = KindleSettingsRepository(db)
    deliveries_repo = KindleDeliveriesRepository(db)
    service = KindleService(
        flibusta=FakeFlibusta(content),
        settings_repo=settings_repo,
        deliveries_repo=deliveries_repo,
        email_sender=FakeEmailSender(),
        conversion_service=ConversionService(),
        max_attachment_bytes=4,
        default_format="epub",
        send_rate_limit_per_hour=limit,
        enable_conversion=False,
        conversion_target_format="epub",
    )
    run(db.initialize())
    return db, settings_repo, deliveries_repo, service


def test_missing_kindle_settings_error(tmp_path: Path) -> None:
    _, _, _, service = build_service(tmp_path)
    with pytest.raises(KindleSettingsMissingError):
        run(service.send_book_to_kindle(1, "123"))


def test_preferred_kindle_format_update(tmp_path: Path) -> None:
    _, settings_repo, _, _ = build_service(tmp_path)
    run(settings_repo.upsert(1, "reader@kindle.com"))
    updated = run(settings_repo.update_preferred_format(1, "fb2"))
    assert updated.preferred_kindle_format == "fb2"


def test_max_attachment_size_rejection_is_logged(tmp_path: Path) -> None:
    db, settings_repo, _, service = build_service(tmp_path, content=b"12345")
    run(settings_repo.upsert(1, "reader@kindle.com"))
    with pytest.raises(KindleFileTooLargeError):
        run(service.send_book_to_kindle(1, "123"))

    async def read_status():
        async with db.connect() as conn:
            row = await (await conn.execute("SELECT status FROM kindle_deliveries")).fetchone()
            return row["status"]

    assert run(read_status()) == "failed"


def test_delivery_status_transitions_success(tmp_path: Path) -> None:
    db, settings_repo, _, service = build_service(tmp_path, content=b"book")
    run(settings_repo.upsert(1, "reader@kindle.com"))
    result = run(service.send_book_to_kindle(1, "123"))
    assert result.format == "epub"

    async def read_status():
        async with db.connect() as conn:
            row = await (await conn.execute("SELECT status FROM kindle_deliveries")).fetchone()
            return row["status"]

    assert run(read_status()) == "sent"


def test_rate_limit_counting(tmp_path: Path) -> None:
    _, settings_repo, _, service = build_service(tmp_path, content=b"book", limit=1)
    run(settings_repo.upsert(1, "reader@kindle.com"))
    run(service.send_book_to_kindle(1, "123"))
    with pytest.raises(KindleRateLimitError):
        run(service.create_queued_delivery(1, "124"))


def test_email_sender_builds_message_with_attachment(monkeypatch) -> None:
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.services.email_sender.aiosmtplib.send", fake_send)
    sender = EmailSender(
        host="smtp.example.com",
        port=587,
        username="user",
        password="secret",
        from_email="books@example.com",
        starttls=True,
    )
    run(
        sender.send_attachment(
            to_email="reader@kindle.com",
            subject="Book",
            filename="book.epub",
            content=b"book",
            content_type="application/epub+zip",
        )
    )
    assert captured["message"]["To"] == "reader@kindle.com"
    assert "private library bot" in captured["message"].get_body(preferencelist=("plain",)).get_content()
    assert captured["message"].iter_attachments().__next__().get_filename() == "book.epub"
    assert captured["kwargs"]["start_tls"] is True


class FakeBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, text, *, chat_id, message_id):
        self.edits.append((text, chat_id, message_id))


def test_queue_enqueue_behavior(tmp_path: Path) -> None:
    _, settings_repo, deliveries_repo, service = build_service(tmp_path, content=b"book")
    run(settings_repo.upsert(1, "reader@kindle.com"))

    async def scenario():
        queue = KindleQueue(service=service)
        bot = FakeBot()
        await queue.start(bot)
        delivery_id = await queue.enqueue(user_id=1, chat_id=2, book_id="123", status_message_id=3)
        await queue._queue.join()
        await queue.stop()
        return delivery_id, await deliveries_repo.get_recent_for_user(1)

    delivery_id, deliveries = run(scenario())
    assert delivery_id > 0
    assert deliveries[0].status == "sent"


def test_user_facing_error_mapping() -> None:
    assert "слишком большой" in user_message_for_exception(KindleFileTooLargeError()).lower()
    assert "не настроена" in user_message_for_exception(EmailConfigurationError()).lower()


def test_history_formatting(tmp_path: Path) -> None:
    _, _, repo, _ = build_service(tmp_path)
    delivery_id = run(repo.create_delivery(1, "123"))
    run(repo.update_status(delivery_id, "failed", title="Book", format="epub", error="smtp exploded badly"))
    text = format_history(run(repo.get_recent_for_user(1)))
    assert "Book [epub] — failed" in text
    assert "smtp exploded badly" in text

def test_migrations_are_idempotent(tmp_path: Path) -> None:
    db=Database(str(tmp_path/'m.db')); run(db.initialize()); run(db.initialize())
    async def names():
        async with db.connect() as c:
            return [r[0] for r in await (await c.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()]
    tables=run(names()); assert 'schema_migrations' in tables and 'user_preferences' in tables

def test_legacy_prefs_import_and_rename(tmp_path: Path) -> None:
    from app.repositories.user_preferences import UserPreferencesRepository
    db=Database(str(tmp_path/'m.db')); run(db.initialize()); p=tmp_path/'user_prefs.json'; p.write_text('{"7":{"preferred_format":"fb2"}}')
    repo=UserPreferencesRepository(db); assert run(repo.import_json_once(p))==1; assert not p.exists(); assert (tmp_path/'user_prefs.json.migrated').exists(); assert run(repo.get(7)).preferred_download_format=='fb2'


def test_mask_smtp_identity() -> None:
    assert mask_smtp_identity("sender@example.com") == "s***@example.com"
    assert mask_smtp_identity("smtp-user") == "sm***"
    assert mask_smtp_identity(None) == "not configured"


def test_kindle_sender_confirmation_flag(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "m.db"))
    run(db.initialize())
    repo = KindleSettingsRepository(db)
    settings = run(repo.upsert(1, "reader@kindle.com"))
    assert settings.approved_sender_confirmed is False
    confirmed = run(repo.set_approved_sender_confirmed(1, True))
    assert confirmed is not None
    assert confirmed.approved_sender_confirmed is True


def test_kindle_messages_are_button_first_without_ses_copy() -> None:
    from app.messages.kindle import kindle_home_text, kindle_missing_email_text, kindle_setup_text

    setup = kindle_setup_text("books@example.com", "gmail")
    assert "Сохранить Kindle e-mail" in setup
    assert "Amazon SES" not in setup
    assert "/kindle" not in setup and "/kindle_email" not in setup
    home = kindle_home_text(None, "books@example.com", "gmail")
    assert "Статус" in home
    assert "Amazon SES" not in home
    assert "/kindle" not in home and "/kindle_email" not in home
    missing = kindle_missing_email_text()
    assert "Kindle ещё не настроен" in missing
    assert "/kindle" not in missing and "/kindle_email" not in missing


def test_env_templates_do_not_contain_real_password() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (".env.gmail.example", ".env.production.example"):
        text = (root / name).read_text()
        assert "your-google-app-password" in text
        assert "your.dedicated.gmail@gmail.com" in text


class CapturingEmailSender:
    def __init__(self):
        self.kwargs = None

    async def send_attachment(self, **kwargs):
        self.kwargs = kwargs


class FormatFlibusta(FakeFlibusta):
    def __init__(self, fmt="epub", content=b"book"):
        super().__init__(content)
        self.fmt = fmt

    async def details(self, book_id: str) -> BookDetails:
        return BookDetails(
            book_id=book_id,
            title="Clean Title",
            authors=["Clean Author"],
            author_refs=[],
            translators=[],
            illustrators=[],
            genres=[],
            file_size=None,
            pages=None,
            annotation=None,
            formats=[DownloadFormat(self.fmt, self.fmt.upper(), f"{self.fmt}-url")],
            page_url="page",
            cover_url="https://example.com/cover.jpg",
        )

    async def download(self, url: str, max_bytes: int):
        return self.content, f"raw.{self.fmt}", "application/epub+zip" if self.fmt == "epub" else "application/octet-stream"


class FakePolisher:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def polish(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("polish failed")
        from app.services.ebook_metadata import PolishedEbook
        return PolishedEbook(b"polished", kwargs["filename"], kwargs["source_format"], True, kwargs["cover_image"] is not None, "fake")


class FakeCoverResolver:
    async def resolve(self, **kwargs):
        from app.services.covers.types import BookCover
        return BookCover("https://example.com/cover.jpg", "fake", 400, 600, 0.9)


def test_kindle_epub_calls_polisher_and_sends_polished_content(tmp_path: Path, monkeypatch) -> None:
    async def fake_download_cover(*args, **kwargs):
        from app.services.covers.types import CoverImage
        return CoverImage(b"cover", "image/jpeg", 400, 600, "cover.jpg", "https://example.com/cover.jpg")

    monkeypatch.setattr("app.services.kindle.download_cover", fake_download_cover)
    db = Database(str(tmp_path / "bot.db")); run(db.initialize())
    settings_repo = KindleSettingsRepository(db); deliveries_repo = KindleDeliveriesRepository(db)
    run(settings_repo.upsert(1, "reader@kindle.com"))
    sender = CapturingEmailSender(); polisher = FakePolisher()
    service = KindleService(
        flibusta=FormatFlibusta("epub"), settings_repo=settings_repo, deliveries_repo=deliveries_repo,
        email_sender=sender, conversion_service=ConversionService(), max_attachment_bytes=1024,
        default_format="epub", send_rate_limit_per_hour=5, enable_conversion=False, conversion_target_format="epub",
        cover_resolver=FakeCoverResolver(), metadata_polisher=polisher, metadata_polish_enabled=True, embed_cover_enabled=True,
        filename_template="{author} - {title}",
    )
    result = run(service.send_book_to_kindle(1, "42"))
    assert polisher.calls
    assert polisher.calls[0]["title"] == "Clean Title"
    assert polisher.calls[0]["authors"] == ["Clean Author"]
    assert polisher.calls[0]["cover_image"] is not None
    assert sender.kwargs["content"] == b"polished"
    assert sender.kwargs["filename"] == "Clean Author - Clean Title.epub"
    assert result.filename == "Clean Author - Clean Title.epub"


def test_kindle_non_epub_bypasses_polisher(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "bot.db")); run(db.initialize())
    settings_repo = KindleSettingsRepository(db); deliveries_repo = KindleDeliveriesRepository(db)
    run(settings_repo.upsert(1, "reader@kindle.com", preferred_format="txt"))
    sender = CapturingEmailSender(); polisher = FakePolisher()
    service = KindleService(
        flibusta=FormatFlibusta("txt"), settings_repo=settings_repo, deliveries_repo=deliveries_repo,
        email_sender=sender, conversion_service=ConversionService(), max_attachment_bytes=1024,
        default_format="epub", send_rate_limit_per_hour=5, enable_conversion=False, conversion_target_format="epub",
        metadata_polisher=polisher, metadata_polish_enabled=True,
    )
    run(service.send_book_to_kindle(1, "42"))
    assert polisher.calls == []
    assert sender.kwargs["content"] == b"book"
    assert sender.kwargs["filename"] == "Clean Author - Clean Title.txt"


def test_kindle_polisher_failure_sends_raw_content(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "bot.db")); run(db.initialize())
    settings_repo = KindleSettingsRepository(db); deliveries_repo = KindleDeliveriesRepository(db)
    run(settings_repo.upsert(1, "reader@kindle.com"))
    sender = CapturingEmailSender(); polisher = FakePolisher(fail=True)
    service = KindleService(
        flibusta=FormatFlibusta("epub"), settings_repo=settings_repo, deliveries_repo=deliveries_repo,
        email_sender=sender, conversion_service=ConversionService(), max_attachment_bytes=1024,
        default_format="epub", send_rate_limit_per_hour=5, enable_conversion=False, conversion_target_format="epub",
        metadata_polisher=polisher, metadata_polish_enabled=True,
    )
    run(service.send_book_to_kindle(1, "42"))
    assert sender.kwargs["content"] == b"book"
    assert sender.kwargs["filename"] == "Clean Author - Clean Title.epub"
