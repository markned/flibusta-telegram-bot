from io import BytesIO
import zipfile

from app.flibusta import (
    _unzip_fb2_if_needed,
    parse_author_page,
    parse_author_results,
    parse_book_details,
    parse_search_results,
)
from app.pagination import page_items, total_pages


def test_parse_search_results() -> None:
    markup = """
    <ul>
      <li><a href="/b/123">Мастер и Маргарита</a> <a href="/a/1">Михаил Булгаков</a></li>
      <li><a href="/b/456">Белая гвардия</a> <a href="/a/1">Михаил Булгаков</a></li>
    </ul>
    """

    results = parse_search_results(markup)

    assert [item.book_id for item in results] == ["123", "456"]
    assert results[0].title == "Мастер и Маргарита"
    assert results[0].author == "Михаил Булгаков"


def test_parse_author_results() -> None:
    markup = """
    <ul>
      <li><a href="/a/10">Анджей Сапковский</a></li>
      <li><a href="/a/11">Алексей Пехов</a></li>
    </ul>
    """

    results = parse_author_results(markup)

    assert [item.author_id for item in results] == ["10", "11"]
    assert results[0].name == "Анджей Сапковский"


def test_parse_book_details() -> None:
    markup = """
    <h1>Мастер и Маргарита</h1>
    <a href="/a/1">Михаил Булгаков</a>
    <a href="/g/1">Роман</a>
    <h2>Аннотация</h2>
    <p>Роман о визите Воланда в Москву.</p>
    <p>Мастер и Маргарита 20K, 11 с. (читать)</p>
    <a href="/b/123/fb2">fb2</a>
    <a href="/b/123/epub">epub</a>
    """

    details = parse_book_details(markup, "https://flibusta.is", "123", "https://flibusta.is/b/123")

    assert details.title == "Мастер и Маргарита"
    assert details.authors == ["Михаил Булгаков"]
    assert [(item.author_id, item.name) for item in details.author_refs] == [("1", "Михаил Булгаков")]
    assert details.genres == ["Роман"]
    assert details.file_size == "20K"
    assert details.pages == 11
    assert details.annotation == "Роман о визите Воланда в Москву."
    assert [item.code for item in details.formats] == ["fb2", "epub"]
    assert details.formats[0].url == "https://flibusta.is/b/123/fb2"


def test_parse_book_details_ignores_site_heading_and_read_link() -> None:
    markup = """
    <html>
      <head><title>Флибуста</title></head>
      <body>
        <h1>Флибуста</h1>
        <div id="main">
          <h2>Рэй Брэдбери: Вино из одуванчиков</h2>
          <a href="/a/0">[Все]</a>
          <a href="/a/1">Рэй Брэдбери</a>
          <p>Аннотация</p>
          <p>Ray Bradbury. Dandelion Wine. 1957.</p>
          <a href="/b/777/read">читать</a>
          <a href="/b/777/fb2">fb2</a>
          <a href="/b/777/epub">epub</a>
        </div>
      </body>
    </html>
    """

    details = parse_book_details(markup, "https://flibusta.is", "777", "https://flibusta.is/b/777")

    assert details.title == "Вино из одуванчиков"
    assert details.authors == ["Рэй Брэдбери"]
    assert [(item.author_id, item.name) for item in details.author_refs] == [("1", "Рэй Брэдбери")]
    assert [item.code for item in details.formats] == ["fb2", "epub"]


def test_unzip_fb2_if_needed() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("books/test.fb2", "<FictionBook />")

    content, filename, content_type = _unzip_fb2_if_needed(
        buffer.getvalue(),
        "test.fb2.zip",
        "application/zip",
        max_bytes=1024,
    )

    assert content == b"<FictionBook />"
    assert filename == "test.fb2"
    assert content_type == "application/x-fictionbook+xml"


def test_parse_author_page() -> None:
    markup = """
    <html>
      <head><title>Анджей Сапковский - Флибуста</title></head>
      <body>
        <h1>Флибуста</h1>
        <div id="main">
          <a href="/a/10">Анджей Сапковский</a>
          <ul>
            <li><a href="/b/100">Последнее желание</a></li>
            <li><a href="/b/101">Меч предназначения</a></li>
          </ul>
        </div>
      </body>
    </html>
    """

    author_name, books = parse_author_page(markup, "10", limit=40)

    assert author_name == "Анджей Сапковский"
    assert [item.book_id for item in books] == ["100", "101"]
    assert all(item.author == "Анджей Сапковский" for item in books)


def test_parse_author_page_prefers_full_name_from_title() -> None:
    markup = """
    <html>
      <head><title>Никитин Иван Иванович - Флибуста</title></head>
      <body>
        <h1>Флибуста</h1>
        <div id="main">
          <a href="/a/55">Никитин</a>
          <ul>
            <li><a href="/b/201">Книга 1</a></li>
          </ul>
        </div>
      </body>
    </html>
    """

    author_name, books = parse_author_page(markup, "55", limit=40)

    assert author_name == "Никитин Иван Иванович"
    assert books[0].author == "Никитин Иван Иванович"


def test_parse_author_page_skips_books_of_other_authors() -> None:
    markup = """
    <html>
      <head><title>Джордж Лукас - Флибуста</title></head>
      <body>
        <div id="main">
          <a href="/a/18326">Джордж Лукас</a>
          <ul>
            <li>
              <a href="/b/301">Звездные войны</a>
              <a href="/a/18326">Джордж Лукас</a>
            </li>
            <li>
              <a href="/b/302">Империя наносит ответный удар</a>
              <a href="/a/18326">Джордж Лукас</a>
            </li>
            <li>
              <a href="/b/999">Невеста по ошибке, или Попаданка для лорда-дракона</a>
              <a href="/a/77777">Лира Серебряная</a>
            </li>
          </ul>
        </div>
      </body>
    </html>
    """

    author_name, books = parse_author_page(markup, "18326", limit=40)

    assert author_name == "Джордж Лукас"
    assert [item.book_id for item in books] == ["301", "302"]


def test_parse_author_page_fallback_div_with_matching_author() -> None:
    markup = """
    <html>
      <head><title>Дейл Карнеги - Флибуста</title></head>
      <body>
        <div id="main">
          <div class="book-entry">
            <a href="/b/422006">Как завоевывать друзей и оказывать влияние на людей</a>
            <span><a href="/a/900">Дейл Карнеги</a></span>
          </div>
          <div class="book-entry">
            <a href="/b/999001">Чужая книга</a>
            <span><a href="/a/901">Другой автор</a></span>
          </div>
        </div>
      </body>
    </html>
    """

    author_name, books = parse_author_page(markup, "900", limit=40)

    assert author_name == "Дейл Карнеги"
    assert [item.book_id for item in books] == ["422006"]


def test_total_pages() -> None:
    assert total_pages(0) == 1
    assert total_pages(8) == 1
    assert total_pages(9) == 2
    assert total_pages(40) == 5


def test_page_items() -> None:
    values = list(range(20))
    assert page_items(values, 0) == list(range(8))
    assert page_items(values, 1) == list(range(8, 16))
    assert page_items(values, 2) == list(range(16, 20))
