from __future__ import annotations

from html import escape
from urllib.parse import quote_plus

from app.flibusta import AuthorResult, BookDetails, SearchResult
from app.branding import BRAND_NAME
from app.web.device import DeviceProfile


BASE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: #111; font: 20px/1.45 Georgia, serif; }
main { width: auto; max-width: 760px; margin: 0 auto; padding: 20px 18px 48px; }
header { border-bottom: 2px solid #111; margin-bottom: 24px; padding-bottom: 12px; }
h1 { font-size: 1.55rem; margin: 0 0 8px; }
h2 { font-size: 1.25rem; margin-top: 28px; }
a { color: #111; text-decoration-thickness: 2px; }
.nav { margin-top: 10px; line-height: 2; }
.nav a { white-space: nowrap; }
.nav-separator { padding: 0 7px; }
.muted { color: #444; }
.notice { border: 2px solid #111; padding: 12px; margin: 16px 0; }
.card { border-bottom: 1px solid #777; padding: 16px 0; }
.card-title { display: block; font-weight: 700; font-size: 1.08rem; margin-bottom: 4px; }
.cover { display: block; max-width: 280px; width: 55%; height: auto; margin: 12px 0 20px; border: 1px solid #777; }
form { margin: 16px 0; }
input[type=search], input[type=text] { width: 100%; min-height: 54px; border: 2px solid #111; border-radius: 0; padding: 10px 12px; background: #fff; color: #111; font: inherit; }
button, .button { display: inline-block; min-height: 50px; border: 2px solid #111; border-radius: 0; padding: 9px 16px; background: #fff; color: #111; font: inherit; font-weight: 700; text-decoration: none; cursor: pointer; }
.primary { background: #111; color: #fff; }
.actions { display: block; margin: 16px 0; }
.actions .button, .actions form { margin: 0 8px 10px 0; vertical-align: top; }
.reader-action { margin: 18px 0; }
.reader-action button { display: block; width: 100%; min-height: 58px; }
.inline { display: inline; margin: 0; }
.meta { margin: 8px 0; color: #333; }
ol, ul { padding-left: 1.3em; }
footer { border-top: 1px solid #777; margin-top: 36px; padding-top: 12px; font-size: .85rem; }
@media (max-width: 520px) { body { font-size: 18px; } main { padding: 14px 12px 36px; } .cover { width: 70%; } button, .button { width: 100%; text-align: center; } .actions .button, .actions form { display: block; width: 100%; margin: 0 0 12px; } .actions form button { width: 100%; } }
"""


def page(title: str, body: str, *, authenticated: bool = False) -> str:
    nav = ""
    if authenticated:
        nav = (
            '<nav class="nav">'
            '<a href="/">Поиск</a><span class="nav-separator">·</span>'
            '<a href="/favorites">Избранное</a><span class="nav-separator">·</span>'
            '<a href="/history">История</a><span class="nav-separator">·</span>'
            '<a href="/readers">Мои устройства</a>'
            "</nav>"
        )
    return (
        "<!doctype html><html lang=\"ru\"><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{BASE_CSS}</style></head><body><main>"
        f"<header><h1><a href=\"/\">{escape(BRAND_NAME)}</a></h1>{nav}</header>"
        f"{body}<footer>Книги не хранятся на сервере постоянно.</footer></main></body></html>"
    )


def login_page(message: str | None = None) -> str:
    notice = f'<div class="notice">{escape(message)}</div>' if message else ""
    return page(
        "Вход",
        f"<h2>{escape(BRAND_NAME)} — вход</h2>"
        "<p>Открой в Telegram раздел <b>Мои устройства → Веб-версия</b> и получи короткий код.</p>"
        f"{notice}"
        '<form method="post" action="/pair">'
        '<label for="code">Код доступа</label>'
        '<input id="code" name="code" type="text" inputmode="text" autocomplete="one-time-code" '
        'maxlength="9" placeholder="ABCD-EFGH" required autofocus>'
        '<p><button class="primary" type="submit">Войти</button></p></form>',
    )


def home_page(*, notice: str | None = None, recent_books: list[SearchResult] | None = None) -> str:
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    recent_html = ""
    if recent_books:
        recent_html = "<h2>Недавние книги</h2>" + book_list(recent_books[:6])
    return page(
        "Поиск книг",
        f"{notice_html}<h2>Найти книгу</h2>"
        '<form method="get" action="/search">'
        '<input name="q" type="search" placeholder="Название книги или автор" required autofocus>'
        '<p><button class="primary" type="submit">Искать</button></p></form>'
        '<p class="muted">Например: «Дюна», «Пелевин» или «исповедь толстой».</p>'
        f"{recent_html}",
        authenticated=True,
    )


def search_page(
    query: str,
    books: list[SearchResult],
    authors: list[AuthorResult],
    *,
    error: str | None = None,
) -> str:
    if error:
        content = f'<div class="notice">{escape(error)}</div>'
    elif not books and not authors:
        content = "<p>Ничего не нашлось. Попробуй сократить запрос.</p>"
    else:
        content = ""
        if books:
            content += "<h2>Книги</h2>" + book_list(books[:20])
        if authors:
            content += "<h2>Авторы</h2>" + author_list(authors[:12])
    return page(
        f"Поиск: {query}",
        "<h2>Результаты поиска</h2>"
        f"<p>Запрос: <b>{escape(query)}</b></p>{content}"
        '<p><a class="button" href="/">Новый поиск</a></p>',
        authenticated=True,
    )


def book_list(books: list[SearchResult]) -> str:
    items = []
    for book in books:
        author = f'<div class="muted">{escape(book.author)}</div>' if book.author else ""
        items.append(
            '<article class="card">'
            f'<a class="card-title" href="/book/{quote_plus(book.book_id)}">{escape(book.title)}</a>'
            f"{author}</article>"
        )
    return "".join(items)


def author_list(authors: list[AuthorResult]) -> str:
    return "".join(
        '<article class="card">'
        f'<a class="card-title" href="/author/{quote_plus(author.author_id)}">{escape(author.name)}</a>'
        "</article>"
        for author in authors
    )


def book_page(
    details: BookDetails,
    *,
    device: DeviceProfile,
    favorite: bool,
    kindle_configured: bool,
    pocketbook_configured: bool,
    notice: str | None = None,
    cover_url: str | None = None,
) -> str:
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    cover = ""
    effective_cover_url = details.cover_url if cover_url is None else cover_url
    if effective_cover_url and effective_cover_url.startswith(("/", "http://", "https://")):
        cover = f'<img class="cover" src="{escape(effective_cover_url, quote=True)}" alt="Обложка">'
    authors = ", ".join(details.authors) or "Автор не указан"
    metadata = []
    if details.genres:
        metadata.append(", ".join(details.genres[:5]))
    if details.file_size:
        metadata.append(details.file_size)
    if details.pages:
        metadata.append(f"{details.pages} с.")
    meta_html = f'<p class="meta">{escape(" · ".join(metadata))}</p>' if metadata else ""
    series_html = ""
    if details.series:
        labels = [item.name + (f" #{item.position}" if item.position else "") for item in details.series[:2]]
        series_html = f'<p><b>Серия:</b> {escape(", ".join(labels))}</p>'
        links = [f'<a class="button" href="/series/{quote_plus(item.series_id)}">Книги серии</a>' for item in details.series[:2] if item.series_id]
        if links:
            series_html += '<div class="actions">' + "".join(links) + "</div>"
    annotation = ""
    if details.annotation:
        text = " ".join(details.annotation.split())
        annotation = f"<h2>Описание</h2><p>{escape(text[:3000])}{'…' if len(text) > 3000 else ''}</p>"

    actions: list[str] = []
    if device.is_reader:
        actions.append(f'<p class="muted">Режим устройства: {escape(device.label)}.</p>')
        provider = device.reader_provider
        configured = kindle_configured if provider == "kindle" else pocketbook_configured if provider == "pocketbook" else False
        if provider and configured:
            label = "Добавить на Kindle" if provider == "kindle" else "Добавить на PocketBook"
            actions.append('<div class="reader-action">')
            actions.append(_post_button(f"/send/{provider}/{details.book_id}", label))
            actions.append("</div>")
        elif provider:
            actions.append(
                f'<p class="notice">{escape(device.label)} ещё не настроен. '
                "Настрой отправку один раз в Telegram.</p>"
            )
        else:
            actions.append('<p class="notice">Автоматическая отправка на эту читалку пока не поддерживается.</p>')
    else:
        actions.append('<div class="actions">')
        for item in _ordered_formats(details):
            actions.append(
                f'<a class="button" href="/download/{quote_plus(details.book_id)}/{quote_plus(item.code)}">'
                f"Скачать {escape(item.code.upper())}</a>"
            )
        actions.append("</div>")
        if kindle_configured or pocketbook_configured:
            actions.append('<h2>Отправить на читалку</h2><div class="actions">')
            if kindle_configured:
                actions.append(_post_button(f"/send/kindle/{details.book_id}", "Отправить на Kindle"))
            if pocketbook_configured:
                actions.append(_post_button(f"/send/pocketbook/{details.book_id}", "Отправить на PocketBook"))
            actions.append("</div>")
        else:
            actions.append('<p class="notice">Читалка ещё не настроена. Настрой её один раз в Telegram.</p>')

        fav_action = "remove" if favorite else "add"
        fav_label = "Удалить из избранного" if favorite else "Добавить в избранное"
        actions.append(_post_button(f"/favorite/{fav_action}/{details.book_id}", fav_label))
    return page(
        details.title,
        f"{notice_html}<article><h2>{escape(details.title)}</h2>"
        f"<p><b>{escape(authors)}</b></p>{cover}{series_html}{meta_html}"
        f"{''.join(actions)}{annotation}</article>",
        authenticated=True,
    )


def readers_page(*, kindle: str | None, pocketbook: str | None) -> str:
    def state(label: str, value: str | None) -> str:
        return f'<article class="card"><b>{escape(label)}</b><div>{escape(value or "не настроен")}</div></article>'

    return page(
        "Мои устройства",
        "<h2>Мои устройства</h2>"
        f"{state('Kindle', kindle)}{state('PocketBook', pocketbook)}"
        '<p class="muted">Адреса и формат меняются в Telegram. Здесь можно искать, скачивать и отправлять книги.</p>'
        '<h2>Доступ</h2>'
        '<form method="post" action="/logout"><button type="submit">Выйти на этом устройстве</button></form>',
        authenticated=True,
    )


def delivery_success_page(details: BookDetails, *, provider: str) -> str:
    label = "PocketBook" if provider == "pocketbook" else "Kindle"
    return page(
        "Книга отправляется",
        '<div class="notice"><h2>Готово</h2>'
        f'<p><b>{escape(details.title)}</b> добавлена в очередь отправки на {escape(label)}.</p>'
        '<p>Обычно книга появляется на устройстве через несколько минут.</p></div>'
        f'<p><a class="button primary" href="/book/{quote_plus(details.book_id)}">Вернуться к книге</a></p>'
        '<p><a class="button" href="/">На главную</a></p>',
        authenticated=True,
    )


def simple_books_page(title: str, books: list[SearchResult], empty_text: str) -> str:
    body = f"<h2>{escape(title)}</h2>"
    body += book_list(books) if books else f"<p>{escape(empty_text)}</p>"
    return page(title, body, authenticated=True)


def favorites_page(books: list[SearchResult], *, count: int, page_number: int, query: str, sort: str) -> str:
    body = f"<h2>Избранное</h2><p>Книг: {count}</p>"
    body += ('<form method="get" action="/favorites"><input name="q" type="search" placeholder="Название или автор" value="' + escape(query, quote=True) + '">'
             '<p><button type="submit">Найти в избранном</button></p></form>')
    body += '<p><a href="/favorites?sort=new">Новые</a> · <a href="/favorites?sort=title">По названию</a> · <a href="/favorites?sort=author">По автору</a></p>'
    body += book_list(books) if books else "<p>Ничего не найдено.</p>"
    params = f"sort={quote_plus(sort)}&q={quote_plus(query)}"
    if page_number > 0:
        body += f'<a class="button" href="/favorites?{params}&page={page_number - 1}">Назад</a> '
    if (page_number + 1) * 20 < count:
        body += f'<a class="button" href="/favorites?{params}&page={page_number + 1}">Дальше</a>'
    return page("Избранное", body, authenticated=True)


def history_page(items) -> str:
    body = "<h2>История</h2>"
    if not items:
        body += "<p>Пока пусто.</p>"
    else:
        for item in items:
            target = {"kindle": "Kindle", "pocketbook": "PocketBook", "telegram": "Telegram", "web": "браузер"}.get(item.delivery_target, item.delivery_target)
            body += (
                '<article class="card">'
                f'<a class="card-title" href="/book/{quote_plus(item.book_id)}">{escape(item.title or "Книга")}</a>'
                f'<div>{escape(item.author or "Автор не указан")}</div>'
                f'<div class="muted">{escape(item.created_at[:16].replace("T", " "))} · {escape(item.format.upper())} · {escape(target)}</div>'
                "</article>"
            )
    return page("История", body, authenticated=True)


def _ordered_formats(details: BookDetails):
    priority = {"epub": 0, "fb2": 1, "txt": 2, "pdf": 3, "mobi": 4}
    return sorted(details.formats, key=lambda item: (priority.get(item.code, 20), item.code))


def _post_button(action: str, label: str) -> str:
    return (
        f'<form class="inline" method="post" action="{escape(action, quote=True)}">'
        f'<button type="submit">{escape(label)}</button></form>'
    )
