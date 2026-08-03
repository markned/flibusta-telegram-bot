import asyncio
from pathlib import Path

import pytest

from app.flibusta import BookDetails, DownloadFormat
from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.repositories.reader_settings import ReaderSettingsRepository
from app.services.conversion import ConversionService
from app.services.kindle import KindleService, choose_reader_format, validate_pocketbook_email


def run(coro):
    return asyncio.run(coro)


def test_pocketbook_email_accepts_only_pbsync() -> None:
    assert validate_pocketbook_email("reader@pbsync.com") == "reader@pbsync.com"
    with pytest.raises(Exception):
        validate_pocketbook_email("reader@example.com")


def test_reader_settings_persist_and_delete(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "bot.db")); run(db.initialize())
    repo = ReaderSettingsRepository(db)
    saved = run(repo.upsert(1, "pocketbook", "reader@pbsync.com", preferred_format="fb2"))
    assert saved.destination_email == "reader@pbsync.com"
    assert run(repo.get(1, "pocketbook")).preferred_format == "fb2"
    run(repo.set_sender_confirmed(1, "pocketbook", True))
    assert run(repo.get(1, "pocketbook")).sender_confirmed is True
    run(repo.delete(1, "pocketbook"))
    assert run(repo.get(1, "pocketbook")) is None


def test_reader_migration_keeps_old_kindle_delivery_provider(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "bot.db")); run(db.initialize())
    deliveries = KindleDeliveriesRepository(db)
    delivery_id = run(deliveries.create_delivery(1, "42"))
    assert run(deliveries.get_by_id(delivery_id)).provider == "kindle"


def test_pocketbook_format_priority_prefers_fb2_over_pdf() -> None:
    formats = [
        DownloadFormat("pdf", "PDF", "pdf"),
        DownloadFormat("fb2", "FB2", "fb2"),
    ]
    assert choose_reader_format(formats, "epub", "pocketbook").code == "fb2"


class Flibusta:
    async def details(self, book_id):
        return BookDetails(
            book_id=book_id,
            title="Книга",
            authors=["Автор"],
            author_refs=[],
            translators=[],
            illustrators=[],
            genres=[],
            file_size=None,
            pages=None,
            annotation=None,
            formats=[DownloadFormat("epub", "EPUB", "url")],
            page_url="page",
        )

    async def download(self, url, max_bytes):
        return b"book", "book.epub", "application/epub+zip"


class Sender:
    def __init__(self):
        self.calls = []

    async def send_attachment(self, **kwargs):
        self.calls.append(kwargs)


def test_pocketbook_delivery_reuses_queue_service_and_logs_provider(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "bot.db")); run(db.initialize())
    kindle_repo = KindleSettingsRepository(db)
    readers_repo = ReaderSettingsRepository(db)
    deliveries = KindleDeliveriesRepository(db)
    run(readers_repo.upsert(1, "pocketbook", "reader@pbsync.com"))
    sender = Sender()
    service = KindleService(
        flibusta=Flibusta(),
        settings_repo=kindle_repo,
        reader_settings_repo=readers_repo,
        deliveries_repo=deliveries,
        email_sender=sender,
        conversion_service=ConversionService(),
        max_attachment_bytes=1024,
        default_format="epub",
        send_rate_limit_per_hour=5,
        enable_conversion=False,
        conversion_target_format="epub",
        metadata_polish_enabled=False,
    )
    delivery_id = run(service.create_queued_delivery(1, "42", provider="pocketbook"))
    result = run(service.process_delivery(delivery_id=delivery_id, user_id=1, book_id="42", provider="pocketbook"))
    assert result.format == "epub"
    assert sender.calls[0]["to_email"] == "reader@pbsync.com"
    delivery = run(deliveries.get_by_id(delivery_id))
    assert delivery.provider == "pocketbook" and delivery.status == "sent"


def test_reader_book_card_uses_generic_send_button() -> None:
    from app.ui.library import formats_keyboard

    details = BookDetails(
        book_id="42", title="Книга", authors=["Автор"], author_refs=[], translators=[], illustrators=[],
        genres=[], file_size=None, pages=None, annotation=None,
        formats=[DownloadFormat("epub", "EPUB", "url")], page_url="page",
    )
    keyboard = formats_keyboard(details, "epub", False, 1200)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert any(button.text == "📤 На читалку" and button.callback_data == "reader_send:42" for button in buttons)
