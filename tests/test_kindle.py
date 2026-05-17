import asyncio
from pathlib import Path

import pytest

from app.flibusta import BookDetails, DownloadFormat
from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.services.email_sender import EmailSender
from app.services.kindle import (
    KindleAttachmentTooLargeError,
    KindleService,
    MissingKindleSettingsError,
    choose_best_format,
    mask_email,
    validate_kindle_email,
)


def run(coro):
    return asyncio.run(coro)


def test_validate_kindle_email_accepts_only_kindle_domains() -> None:
    assert validate_kindle_email("reader@kindle.com") == "reader@kindle.com"
    assert validate_kindle_email("reader@free.kindle.com") == "reader@free.kindle.com"
    with pytest.raises(Exception):
        validate_kindle_email("reader@gmail.com")


def test_mask_email() -> None:
    assert mask_email("mark@kindle.com") == "m***@kindle.com"


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


def build_service(tmp_path: Path, *, content: bytes = b"book"):
    db = Database(str(tmp_path / "bot.db"))
    settings_repo = KindleSettingsRepository(db)
    deliveries_repo = KindleDeliveriesRepository(db)
    service = KindleService(
        flibusta=FakeFlibusta(content),
        settings_repo=settings_repo,
        deliveries_repo=deliveries_repo,
        email_sender=FakeEmailSender(),
        max_attachment_bytes=4,
        default_format="epub",
        send_rate_limit_per_hour=5,
    )
    run(db.initialize())
    return db, settings_repo, deliveries_repo, service


def test_missing_kindle_settings_error(tmp_path: Path) -> None:
    _, _, _, service = build_service(tmp_path)
    with pytest.raises(MissingKindleSettingsError):
        run(service.send_book_to_kindle(1, "123"))


def test_max_attachment_size_rejection_is_logged(tmp_path: Path) -> None:
    db, settings_repo, _, service = build_service(tmp_path, content=b"12345")
    run(settings_repo.upsert(1, "reader@kindle.com"))
    with pytest.raises(KindleAttachmentTooLargeError):
        run(service.send_book_to_kindle(1, "123"))

    async def read_status():
        async with db.connect() as conn:
            row = await (await conn.execute("SELECT status FROM kindle_deliveries")).fetchone()
            return row["status"]

    assert run(read_status()) == "failed"


def test_delivery_status_logging_success(tmp_path: Path) -> None:
    db, settings_repo, _, service = build_service(tmp_path, content=b"book")
    run(settings_repo.upsert(1, "reader@kindle.com"))
    result = run(service.send_book_to_kindle(1, "123"))
    assert result.format == "epub"

    async def read_status():
        async with db.connect() as conn:
            row = await (await conn.execute("SELECT status FROM kindle_deliveries")).fetchone()
            return row["status"]

    assert run(read_status()) == "sent"


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
