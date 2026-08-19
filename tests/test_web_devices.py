from app.flibusta import AuthorResult, BookDetails, DownloadFormat
from app.web import ui
from app.web.device import ANDROID, DESKTOP, IOS, KINDLE, POCKETBOOK, detect_device


def _details(*, cover_url: str | None = "https://img.example/cover.jpg") -> BookDetails:
    return BookDetails(
        book_id="10",
        title="Дюна",
        authors=["Фрэнк Герберт"],
        author_refs=[AuthorResult("20", "Фрэнк Герберт")],
        translators=[],
        illustrators=[],
        genres=["Фантастика"],
        file_size="1 МБ",
        pages=500,
        annotation="Пустынная планета Арракис.",
        formats=[
            DownloadFormat("epub", "EPUB", "https://example.test/book.epub"),
            DownloadFormat("fb2", "FB2", "https://example.test/book.fb2"),
            DownloadFormat("mobi", "MOBI", "https://example.test/book.mobi"),
        ],
        page_url="https://example.test/b/10",
        cover_url=cover_url,
    )


def test_detects_reader_and_regular_devices() -> None:
    assert detect_device("Mozilla/5.0 Kindle/5.17.1") == KINDLE
    assert detect_device("Mozilla/5.0 Silk/3.13 Safari/535.19") == KINDLE
    assert detect_device("Mozilla/5.0 (Linux; PocketBook 740)") == POCKETBOOK
    assert detect_device("Mozilla/5.0 (Linux; Android 14)") == ANDROID
    assert detect_device("Mozilla/5.0 (iPad; CPU OS 17_0)") == IOS
    assert detect_device("Mozilla/5.0 (X11; Linux x86_64)") == DESKTOP


def test_kindle_page_only_offers_send_to_kindle() -> None:
    html = ui.book_page(
        _details(),
        device=KINDLE,
        favorite=False,
        kindle_configured=True,
        pocketbook_configured=True,
    )

    assert "Добавить на Kindle" in html
    assert "/send/kindle/10" in html
    assert "/send/pocketbook/10" not in html
    assert "/download/" not in html
    assert "Добавить в избранное" not in html
    assert '<img class="cover"' in html


def test_pocketbook_page_only_offers_send_to_pocketbook() -> None:
    html = ui.book_page(
        _details(),
        device=POCKETBOOK,
        favorite=False,
        kindle_configured=True,
        pocketbook_configured=True,
    )

    assert "Добавить на PocketBook" in html
    assert "/send/pocketbook/10" in html
    assert "/send/kindle/10" not in html
    assert "/download/" not in html
    assert '<img class="cover"' in html


def test_reader_without_settings_shows_setup_hint_not_downloads() -> None:
    html = ui.book_page(
        _details(),
        device=KINDLE,
        favorite=False,
        kindle_configured=False,
        pocketbook_configured=False,
    )

    assert "Kindle ещё не настроен" in html
    assert "Настрой отправку один раз в Telegram" in html
    assert "/download/" not in html


def test_regular_device_keeps_downloads_reader_actions_and_cover() -> None:
    html = ui.book_page(
        _details(),
        device=ANDROID,
        favorite=False,
        kindle_configured=True,
        pocketbook_configured=True,
    )

    assert "Скачать EPUB" in html
    assert "Скачать FB2" in html
    assert "Скачать MOBI" in html
    assert "Отправить на Kindle" in html
    assert "Отправить на PocketBook" in html
    assert "Добавить в избранное" in html
    assert '<img class="cover"' in html


def test_missing_cover_never_renders_image() -> None:
    html = ui.book_page(
        _details(cover_url=None),
        device=DESKTOP,
        favorite=False,
        kindle_configured=False,
        pocketbook_configured=False,
    )
    assert '<img class="cover"' not in html


def test_css_avoids_flex_and_min_function_for_old_reader_browsers() -> None:
    assert "display: flex" not in ui.BASE_CSS
    assert "width: min(" not in ui.BASE_CSS
    assert 'class="nav-separator"' in ui.page("Test", "", authenticated=True)
