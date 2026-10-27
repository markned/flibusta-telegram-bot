from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, replace
import logging
import re
from time import monotonic
from typing import Any
from urllib.parse import quote, urlencode

from aiohttp import web

from app.flibusta import FlibustaError, SearchResult
from app.repositories.access import AccessRepository
from app.repositories.download_history import DownloadHistoryRepository
from app.repositories.favorites import FavoritesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.repositories.last_books import LastBooksRepository
from app.repositories.reader_settings import ReaderSettingsRepository
from app.repositories.web_access import WebAccessRepository
from app.services.kindle import mask_email, sanitize_filename
from app.services.search.types import outcome_notice
from app.services.catalog_browse import browse_catalog
from app.services.popular_collections import SOURCES
from app.services.works import group_books, choose_default, editions, edition_order
from app.services.work_registry import WorkRegistry
from app.services.covers.download import CoverDownloadError, download_cover
from app.web.device import detect_device
from app.web import ui


logger = logging.getLogger(__name__)
SESSION_COOKIE = "flibusta_web_session"
WORKS_KEY = web.AppKey("works", WorkRegistry)


@dataclass(frozen=True)
class WebDependencies:
    search_service: Any
    flibusta: Any
    web_access_repo: WebAccessRepository
    access_repo: AccessRepository
    favorites_repo: FavoritesRepository
    history_repo: DownloadHistoryRepository
    last_books_repo: LastBooksRepository
    kindle_settings_repo: KindleSettingsRepository
    reader_settings_repo: ReaderSettingsRepository
    delivery_queue: Any
    admin_ids: set[int]
    access_control_enabled: bool
    session_days: int
    max_sessions_per_user: int
    download_max_bytes: int
    download_concurrency: int
    search_rate_limit_per_minute: int
    download_rate_limit_per_hour: int
    cookie_secure: bool
    cover_resolver: Any | None = None
    cover_download_max_bytes: int = 3 * 1024 * 1024
    cover_download_timeout_seconds: float = 6
    search_stats_repo: Any | None = None
    popular_collections: Any | None = None


class PairRateLimiter:
    def __init__(self, limit: int = 10, window_seconds: int = 60, max_keys: int = 1000) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = monotonic()
        attempts = self._attempts[key]
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        if len(attempts) >= self.limit:
            return False
        attempts.append(now)
        if len(self._attempts) > self.max_keys:
            for stale_key in list(self._attempts)[: len(self._attempts) // 4]:
                if not self._attempts[stale_key] or now - self._attempts[stale_key][-1] > self.window_seconds:
                    self._attempts.pop(stale_key, None)
        return True


DEPS_KEY = web.AppKey("deps", WebDependencies)
PAIR_LIMITER_KEY = web.AppKey("pair_limiter", PairRateLimiter)
SEARCH_LIMITER_KEY = web.AppKey("search_limiter", PairRateLimiter)
DOWNLOAD_LIMITER_KEY = web.AppKey("download_limiter", PairRateLimiter)
DOWNLOAD_SEMAPHORE_KEY = web.AppKey("download_semaphore", asyncio.Semaphore)


def build_web_app(deps: WebDependencies) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware, _error_middleware], client_max_size=32 * 1024)
    app[DEPS_KEY] = deps
    app[WORKS_KEY] = WorkRegistry()
    app[PAIR_LIMITER_KEY] = PairRateLimiter()
    app[SEARCH_LIMITER_KEY] = PairRateLimiter(
        limit=max(1, deps.search_rate_limit_per_minute),
        window_seconds=60,
    )
    app[DOWNLOAD_LIMITER_KEY] = PairRateLimiter(
        limit=max(1, deps.download_rate_limit_per_hour),
        window_seconds=3600,
    )
    app[DOWNLOAD_SEMAPHORE_KEY] = asyncio.Semaphore(max(1, deps.download_concurrency))
    app.router.add_get("/health", _health)
    app.router.add_get("/", _home)
    app.router.add_post("/pair", _pair)
    app.router.add_post("/logout", _logout)
    app.router.add_get("/search", _search)
    app.router.add_get("/browse", _browse)
    app.router.add_get("/author/{author_id}", _author)
    app.router.add_get("/series/{series_id}", _series)
    app.router.add_get("/book/{book_id}", _book)
    app.router.add_get("/work/{token}", _work)
    app.router.add_get("/cover/{book_id}", _cover)
    app.router.add_get("/favorites", _favorites)
    app.router.add_post("/favorite/{action}/{book_id}", _favorite_action)
    app.router.add_get("/history", _history)
    app.router.add_get("/last", _last)
    app.router.add_get("/readers", _readers)
    app.router.add_get("/download/{book_id}/{fmt}", _download)
    app.router.add_post("/send/{provider}/{book_id}", _send)
    app.router.add_get("/sent/{provider}/{book_id}", _sent)
    return app


async def start_web_server(
    deps: WebDependencies,
    *,
    host: str,
    port: int,
) -> web.AppRunner:
    runner = web.AppRunner(build_web_app(deps), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("web shell started host=%s port=%s", host, port)
    return runner


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    deps: WebDependencies = request.app[DEPS_KEY]
    token = request.cookies.get(SESSION_COOKIE)
    session = await deps.web_access_repo.get_session(token)
    user_id = session.user_id if session else None
    if user_id is not None and deps.access_control_enabled and user_id not in deps.admin_ids:
        user = await deps.access_repo.get_user(user_id)
        if user is None or user.status != "approved":
            user_id = None
    request["user_id"] = user_id
    if request.path not in {"/", "/pair", "/health"} and user_id is None:
        raise web.HTTPSeeOther("/")
    return await handler(request)


@web.middleware
async def _error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (FlibustaError, TimeoutError):
        logger.warning("web catalog request failed path=%s", request.path)
        return _html(ui.page("Ошибка", '<div class="notice">Библиотека отвечает медленно. Попробуй ещё раз.</div>', authenticated=bool(request.get("user_id"))), status=503)
    except Exception:
        logger.exception("web request failed path=%s", request.path)
        return _html(ui.page("Ошибка", '<div class="notice">Не удалось выполнить запрос. Попробуй ещё раз.</div>', authenticated=bool(request.get("user_id"))), status=500)


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _home(request: web.Request) -> web.Response:
    if request["user_id"] is None:
        return _html(ui.login_page())
    deps: WebDependencies = request.app[DEPS_KEY]
    user_id = int(request["user_id"])
    last, history = await asyncio.gather(
        deps.last_books_repo.get(user_id),
        deps.history_repo.recent(user_id, limit=10),
    )
    recent: list[SearchResult] = []
    seen: set[str] = set()
    if last is not None:
        recent.append(SearchResult(last.book_id, last.title, last.author))
        seen.add(last.book_id)
    for item in history:
        if not item.title or item.book_id in seen:
            continue
        recent.append(SearchResult(item.book_id, item.title, item.author))
        seen.add(item.book_id)
        if len(recent) >= 6:
            break
    return _html(ui.home_page(notice=request.query.get("notice"), recent_books=recent))


async def _pair(request: web.Request) -> web.Response:
    remote = request.headers.get("X-Real-IP") or request.remote or "unknown"
    limiter: PairRateLimiter = request.app[PAIR_LIMITER_KEY]
    if not limiter.allow(remote):
        return _html(ui.login_page("Слишком много попыток. Подожди минуту."), status=429)
    data = await request.post()
    deps: WebDependencies = request.app[DEPS_KEY]
    token = await deps.web_access_repo.consume_pairing_code(
        str(data.get("code", "")),
        session_days=deps.session_days,
        max_sessions_per_user=deps.max_sessions_per_user,
    )
    if token is None:
        return _html(ui.login_page("Код неверный или уже устарел."), status=401)
    response = web.HTTPSeeOther("/")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=deps.session_days * 86400,
        httponly=True,
        secure=deps.cookie_secure,
        samesite="Lax",
        path="/",
    )
    raise response


async def _logout(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    await deps.web_access_repo.revoke_session(request.cookies.get(SESSION_COOKIE))
    response = web.HTTPSeeOther("/")
    response.del_cookie(SESSION_COOKIE, path="/")
    raise response


def _work_links(request, books, return_url):
    return {
        book.book_id: "/work/"
        + request.app[WORKS_KEY].add(int(request["user_id"]), book, return_url)
        for book in books
        if book.editions
    }


async def _work(request):
    ref = request.app[WORKS_KEY].get(
        request.match_info["token"], int(request["user_id"])
    )
    if ref is None:
        return _html(
            ui.page(
                "Выдача устарела",
                "<p>Открой поиск или подборку заново.</p>",
                authenticated=True,
            ),
            status=410,
        )
    if request.query.get("editions") == "1" and hasattr(
        request.app[DEPS_KEY].flibusta, "complete_work"
    ):
        work = await request.app[DEPS_KEY].flibusta.complete_work(ref.work)
        try:
            ref = request.app[WORKS_KEY].set_work(
                request.match_info["token"], int(request["user_id"]), work
            )
        except ValueError:
            raise web.HTTPBadRequest(text="Слишком много изданий. Уточни название.")
        if ref is None:
            raise web.HTTPGone(text="Выдача устарела. Открой её заново.")
    base = "/work/" + request.match_info["token"]
    requested = request.query.get("edition")
    selected = (
        next((e for e in editions(ref.work) if e.book_id == requested), None)
        if requested
        else choose_default(ref.work)
    )
    if requested and selected is None:
        raise web.HTTPBadRequest(text="Неизвестное издание")
    if request.query.get("editions") == "1" or selected is None:
        try:
            page_number = max(0, min(10000, int(request.query.get("page", "0"))))
        except ValueError:
            raise web.HTTPBadRequest(text="Неизвестная страница")
        return _html(ui.editions_page(ref.work, base, ref.return_url, page_number))
    return await _book(request, book_id=selected.book_id, work_ref=ref)


async def _search(request: web.Request) -> web.Response:
    query = " ".join(request.query.get("q", "").split())[:160]
    if not query:
        raise web.HTTPSeeOther("/")
    deps: WebDependencies = request.app[DEPS_KEY]
    if not request.app[SEARCH_LIMITER_KEY].allow(str(request["user_id"])):
        return _html(
            ui.search_page(query, [], [], error="Слишком много запросов. Подожди минуту."),
            status=429,
        )
    expanded = request.query.get("expand") == "1"
    try:
        page_number = max(0, min(100, int(request.query.get("page", "0"))))
    except ValueError:
        page_number = 0
    try:
        if request.query.get("mode") == "about":
            outcome = await deps.search_service.search_about_author(query)
        elif expanded:
            outcome = await deps.search_service.search(query, expanded=True)
        else:
            outcome = await deps.search_service.search(query)
    except (FlibustaError, TimeoutError):
        return _html(ui.search_page(
            query, [], [], error="Источник временно недоступен. Не удалось проверить наличие книги.",
            retry=True, expanded=expanded, about_author=request.query.get("mode") == "about",
        ), status=503)
    if outcome.author_id:
        raise web.HTTPSeeOther(f"/author/{outcome.author_id}?page={page_number}")
    outcome_books=group_books(outcome.books)
    visible_page=min(page_number,max(0,(len(outcome_books)-1)//20))
    links=_work_links(request,outcome_books[visible_page*20:(visible_page+1)*20],str(request.rel_url))
    error = None
    if outcome.plan.mode.value == "unsupported_topic":
        error = "Поиск работает по названию книги, автору или их сочетанию."
    elif not outcome.books and not outcome.authors and not outcome.suggested_query and not outcome.query_choices and not outcome.source_unavailable and deps.search_stats_repo is not None:
        await deps.search_stats_repo.record_miss(query)
    if outcome.source_unavailable and not outcome.books and not outcome.authors:
        error = "Источник временно недоступен. Не удалось проверить наличие книги."
    return _html(ui.search_page(
        query, outcome_books, outcome.authors, error=error,
        notice=outcome_notice(outcome), match_query=outcome.plan.title or outcome.plan.cleaned_query,
        has_more=outcome.has_more, retry=outcome.source_unavailable or outcome.partial,
        expanded=expanded, page_number=page_number,
        author_query=(outcome.plan.author or outcome.plan.cleaned_query) if outcome.plan.mode.value == "author" else None,
        suggested_query=outcome.suggested_query, about_author=outcome.about_author,
        query_choices=outcome.query_choices, clarification=outcome.clarification, work_links=links,
    ), status=503 if error and outcome.source_unavailable else 200)


async def _browse(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    if not request.app[SEARCH_LIMITER_KEY].allow(str(request["user_id"])):
        raise web.HTTPTooManyRequests(text="Слишком много запросов. Подожди минуту.")
    try:
        page_number = min(50, max(0, int(request.query.get("page", "0"))))
        source = request.query.get("source")
        view = request.query.get("view", "tops")
        genre = request.query.get("genre")
        group = request.query.get("group")
        if view == "random":
            book = await deps.popular_collections.random_book(request.query.get("exclude")) if deps.popular_collections else None
            if book:
                raise web.HTTPSeeOther(f"/book/{book.book_id}?collection=random")
            return _html(ui.browse_page("Случайная книга", [], [], view="random"))
        if genre:
            title, books, _, _ = await browse_catalog(deps.flibusta,genre)
            genres=[]
        elif view == "genres":
            title, books, genres = "Жанры", [], await deps.flibusta.genres()
        elif source:
            if source not in SOURCES: raise ValueError("Unknown source")
            title = SOURCES[source]
            books = await deps.popular_collections.books(source) if deps.popular_collections else await deps.flibusta.popular_books() if source == "flibusta" else []
            genres=[]
        else:
            title,books,genres = "Популярное",[],[]
    except ValueError:
        raise web.HTTPBadRequest(text="Неизвестный раздел или страница.")
    books=group_books(books)
    visible_page=min(page_number,max(0,(len(books)-1)//8))
    links=_work_links(request,books[visible_page*8:(visible_page+1)*8],str(request.rel_url))
    return _html(ui.browse_page(title,books,genres,page_number=page_number,genre=genre,
                               source=source,view=view,group=group,work_links=links))


async def _author(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    author_id = _catalog_id(request.match_info["author_id"])
    try:
        page_number = max(0, min(10000, int(request.query.get("page", "0"))))
    except ValueError:
        raise web.HTTPBadRequest(text="Неизвестная страница")
    if hasattr(deps.flibusta, "author_works_page"):
        page = await deps.flibusta.author_works_page(
            author_id, offset=page_number * 8, limit=8
        )
        name, books, total = page.name, page.books, page.total
    else:
        name, all_books = await deps.flibusta.author_books(author_id, limit=40)
        works = group_books(all_books, alphabetical=True)
        books, total = works[page_number * 8 : (page_number + 1) * 8], len(works)
    links = _work_links(request, books, f"/author/{author_id}?page={page_number}")
    return _html(
        ui.author_works_page(name, books, author_id, page_number, total, links)
    )


async def _series(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    series_id = _catalog_id(request.match_info["series_id"])
    name, books = await deps.flibusta.series_books(series_id, limit=80)
    books=group_books(books)
    return _html(ui.simple_books_page(f"Серия: {name}", books, "Книги серии не найдены.",
                                     work_links=_work_links(request,books,str(request.rel_url))))


def _collection_return(request):
    source = request.query.get("collection")
    genre = request.query.get("genre")
    if source == "random": return "/"
    if source not in SOURCES and not genre: return None
    try:
        page = max(0,min(50,int(request.query.get("collection_page","0"))))
    except ValueError: page=0
    params={"page":page}
    if source in SOURCES: params["source"]=source
    if genre and re.fullmatch(r"[a-z0-9_]{1,48}",genre): params["genre"]=genre
    return "/browse?"+urlencode(params)


async def _book(request: web.Request, book_id=None, work_ref=None) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    user_id = int(request["user_id"])
    details = await deps.flibusta.details(
        book_id or _catalog_id(request.match_info["book_id"])
    )
    favorite, kindle, pocketbook = await asyncio.gather(
        deps.favorites_repo.exists(user_id, details.book_id),
        deps.kindle_settings_repo.get(user_id),
        deps.reader_settings_repo.get(user_id, "pocketbook"),
    )
    await deps.last_books_repo.upsert(
        user_id,
        details.book_id,
        details.title,
        ", ".join(details.authors) or None,
        "web_opened",
    )
    cover_url = ""
    if deps.cover_resolver is not None:
        try:
            cover = await deps.cover_resolver.resolve(
                title=details.title,
                authors=details.authors,
                flibusta_cover_url=details.cover_url,
            )
            if cover is not None:
                cover_url = f"/cover/{quote(details.book_id)}"
        except Exception:
            logger.info("web cover lookup skipped book_id=%s", details.book_id)
    editions_url = None
    if work_ref:
        if details.author_refs:
            work_ref = replace(
                work_ref,
                work=replace(
                    work_ref.work, source_author_id=details.author_refs[0].author_id
                ),
            )
            work_ref = request.app[WORKS_KEY].set_work(
                request.match_info["token"], user_id, work_ref.work
            )
            if work_ref is None:
                raise web.HTTPGone(text="Выдача устарела. Открой её заново.")
        editions_url = "/work/" + request.match_info["token"] + "?editions=1"
    elif details.author_refs:
        work = group_books(
            [
                SearchResult(
                    details.book_id,
                    details.title,
                    ", ".join(details.authors),
                    source_author_id=details.author_refs[0].author_id,
                )
            ]
        )[0]
        token = request.app[WORKS_KEY].add(
            user_id, work, _collection_return(request) or str(request.rel_url)
        )
        editions_url = "/work/" + token + "?editions=1"
    return _html(
        ui.book_page(
            details,
            device=detect_device(request.headers.get("User-Agent")),
            favorite=favorite,
            kindle_configured=bool(kindle and kindle.send_to_kindle_enabled),
            pocketbook_configured=bool(pocketbook and pocketbook.enabled),
            notice=request.query.get("notice"),
            cover_url=cover_url,
            return_url=work_ref.return_url if work_ref else _collection_return(request),
            editions_url=editions_url,
            random_next=f"/browse?view=random&exclude={details.book_id}"
            if request.query.get("collection") == "random"
            else None,
        )
    )


async def _cover(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    if deps.cover_resolver is None:
        raise web.HTTPNotFound()
    book_id = _catalog_id(request.match_info["book_id"])
    details = await deps.flibusta.details(book_id)
    cover = await deps.cover_resolver.resolve(
        title=details.title,
        authors=details.authors,
        flibusta_cover_url=details.cover_url,
    )
    if cover is None:
        raise web.HTTPNotFound()
    try:
        image = await download_cover(
            cover.url,
            max_bytes=deps.cover_download_max_bytes,
            timeout=deps.cover_download_timeout_seconds,
        )
    except CoverDownloadError:
        logger.info("web cover proxy failed source=%s book_id=%s", cover.source, book_id)
        raise web.HTTPNotFound()
    return web.Response(
        body=image.content,
        content_type=image.content_type,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


async def _favorites(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    query = " ".join(request.query.get("q", "").split())[:100]
    sort = request.query.get("sort", "new") if request.query.get("sort") in {"new", "title", "author"} else "new"
    page = max(0, min(1000, int(request.query.get("page", "0"))) if request.query.get("page", "0").isdigit() else 0)
    items = await deps.favorites_repo.list(int(request["user_id"]), limit=20, offset=page * 20, sort=sort, query=query)
    count = await deps.favorites_repo.count(int(request["user_id"]), query=query)
    books = [SearchResult(item.book_id, item.title, item.author) for item in items]
    return _html(ui.favorites_page(books, count=count, page_number=page, query=query, sort=sort))


async def _favorite_action(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    user_id = int(request["user_id"])
    book_id = _catalog_id(request.match_info["book_id"])
    action = request.match_info["action"]
    if action == "add":
        details = await deps.flibusta.details(book_id)
        await deps.favorites_repo.add(user_id, book_id, details.title, ", ".join(details.authors) or None)
        notice = "Добавлено в избранное."
    elif action == "remove":
        await deps.favorites_repo.remove(user_id, book_id)
        notice = "Удалено из избранного."
    else:
        raise web.HTTPNotFound()
    raise web.HTTPSeeOther(f"/book/{quote(book_id)}?notice={quote(notice)}")


async def _history(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    items = await deps.history_repo.recent(int(request["user_id"]), limit=30)
    return _html(ui.history_page(items))


async def _last(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    item = await deps.last_books_repo.get(int(request["user_id"]))
    if item is None:
        return _html(ui.simple_books_page("Последняя книга", [], "Ты ещё не открывал книги."))
    raise web.HTTPSeeOther(f"/book/{quote(item.book_id)}")


async def _readers(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    user_id = int(request["user_id"])
    kindle, pocketbook = await asyncio.gather(
        deps.kindle_settings_repo.get(user_id),
        deps.reader_settings_repo.get(user_id, "pocketbook"),
    )
    return _html(
        ui.readers_page(
            kindle=mask_email(kindle.kindle_email) if kindle else None,
            pocketbook=mask_email(pocketbook.destination_email) if pocketbook else None,
        )
    )


async def _download(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    user_id = int(request["user_id"])
    book_id = _catalog_id(request.match_info["book_id"])
    fmt = request.match_info["fmt"].casefold()
    if not re.fullmatch(r"[a-z0-9]{1,12}", fmt):
        raise web.HTTPNotFound()
    if not request.app[DOWNLOAD_LIMITER_KEY].allow(str(user_id)):
        return _html(
            ui.page(
                "Лимит загрузок",
                '<div class="notice">Лимит загрузок достигнут. Попробуй позже.</div>',
                authenticated=True,
            ),
            status=429,
        )
    details = await deps.flibusta.details(book_id)
    target = next((item for item in details.formats if item.code.casefold() == fmt), None)
    if target is None:
        raise web.HTTPNotFound(text="Формат не найден")
    try:
        async with request.app[DOWNLOAD_SEMAPHORE_KEY]:
            content, filename, content_type = await deps.flibusta.download(
                target.url,
                max_bytes=deps.download_max_bytes,
            )
    except Exception as exc:
        await deps.history_repo.add(
            user_id=user_id,
            book_id=book_id,
            title=details.title,
            author=", ".join(details.authors) or None,
            format=fmt,
            filename=None,
            file_size_bytes=None,
            delivery_target="web",
            status="failed",
            error=type(exc).__name__,
        )
        raise
    filename = sanitize_filename(filename, fallback=f"{details.title}.{fmt}")
    await deps.history_repo.add(
        user_id=user_id,
        book_id=book_id,
        title=details.title,
        author=", ".join(details.authors) or None,
        format=fmt,
        filename=filename,
        file_size_bytes=len(content),
        delivery_target="web",
        status="sent",
    )
    await deps.last_books_repo.upsert(
        user_id,
        book_id,
        details.title,
        ", ".join(details.authors) or None,
        "web_downloaded",
    )
    ascii_name = f"book.{fmt}".replace('"', "")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return web.Response(
        body=content,
        content_type=content_type.split(";", 1)[0],
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )


async def _send(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    provider = request.match_info["provider"]
    book_id = _catalog_id(request.match_info["book_id"])
    if provider not in {"kindle", "pocketbook"}:
        raise web.HTTPNotFound()
    try:
        await deps.delivery_queue.enqueue(
            user_id=int(request["user_id"]),
            chat_id=None,
            book_id=book_id,
            status_message_id=None,
            provider=provider,
        )
    except Exception:
        logger.warning("web reader enqueue failed provider=%s book_id=%s", provider, book_id)
        notice = "Не удалось добавить книгу в очередь. Проверь настройки читалки."
        raise web.HTTPSeeOther(f"/book/{quote(book_id)}?notice={quote(notice)}")
    raise web.HTTPSeeOther(f"/sent/{provider}/{quote(book_id)}")


async def _sent(request: web.Request) -> web.Response:
    provider = request.match_info["provider"]
    if provider not in {"kindle", "pocketbook"}:
        raise web.HTTPNotFound()
    deps: WebDependencies = request.app[DEPS_KEY]
    details = await deps.flibusta.details(_catalog_id(request.match_info["book_id"]))
    return _html(ui.delivery_success_page(details, provider=provider))


def _html(content: str, *, status: int = 200) -> web.Response:
    return web.Response(
        text=content,
        content_type="text/html",
        charset="utf-8",
        status=status,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src https: http: data:; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


def _catalog_id(value: str) -> str:
    if not value.isdigit() or len(value) > 20:
        raise web.HTTPNotFound()
    return value
