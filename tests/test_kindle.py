import asyncio
from pathlib import Path

import pytest

from app.flibusta import BookDetails, DownloadFormat
from app.handlers.kindle import format_history, user_message_for_exception
from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.services.conversion import ConversionService
from app.services.email_sender import EmailConfigurationError, EmailSender, validate_smtp_from_email
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
    assert "too large" in user_message_for_exception(KindleFileTooLargeError()).lower()
    assert "temporarily unavailable" in user_message_for_exception(EmailConfigurationError()).lower()


def test_history_formatting(tmp_path: Path) -> None:
    _, _, repo, _ = build_service(tmp_path)
    delivery_id = run(repo.create_delivery(1, "123"))
    run(repo.update_status(delivery_id, "failed", title="Book", format="epub", error="smtp exploded badly"))
    text = format_history(run(repo.get_recent_for_user(1)))
    assert "Book [epub] — failed" in text
    assert "smtp exploded badly" in text
