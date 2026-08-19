from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
import logging
import re
from time import monotonic
from typing import Any
from urllib.parse import quote

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
from app.services.covers.download import CoverDownloadError, download_cover
from app.web.device import detect_device
from app.web import ui


logger = logging.getLogger(__name__)
SESSION_COOKIE = "flibusta_web_session"


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
    app.router.add_get("/author/{author_id}", _author)
    app.router.add_get("/book/{book_id}", _book)
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
    outcome = await deps.search_service.search(query)
    error = None
    if outcome.plan.mode.value == "unsupported_topic":
        error = "Поиск работает по названию книги, автору или их сочетанию."
    return _html(ui.search_page(query, outcome.books, outcome.authors, error=error))


async def _author(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    author_id = _catalog_id(request.match_info["author_id"])
    name, books = await deps.flibusta.author_books(author_id, limit=40)
    return _html(ui.simple_books_page(f"Книги автора: {name}", books, "Книги не найдены."))


async def _book(request: web.Request) -> web.Response:
    deps: WebDependencies = request.app[DEPS_KEY]
    user_id = int(request["user_id"])
    details = await deps.flibusta.details(_catalog_id(request.match_info["book_id"]))
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
    return _html(
        ui.book_page(
            details,
            device=detect_device(request.headers.get("User-Agent")),
            favorite=favorite,
            kindle_configured=bool(kindle and kindle.send_to_kindle_enabled),
            pocketbook_configured=bool(pocketbook and pocketbook.enabled),
            notice=request.query.get("notice"),
            cover_url=cover_url,
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
    items = await deps.favorites_repo.list(int(request["user_id"]), limit=50)
    books = [SearchResult(item.book_id, item.title, item.author) for item in items]
    return _html(ui.simple_books_page("Избранное", books, "В избранном пока пусто."))


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
