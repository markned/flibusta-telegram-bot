from __future__ import annotations

import logging
from asyncio import sleep
from dataclasses import dataclass
from html import escape
from time import monotonic
from typing import Awaitable, Callable, TypeVar
from urllib.parse import urlparse
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.types import User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Settings
from app.flibusta import AuthorResult, BookDetails, FlibustaClient, FlibustaError
from app.pagination import SEARCH_PAGE_SIZE, page_items, total_pages

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
if settings.log_level.upper() != "DEBUG":
    logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
T = TypeVar("T")

router = Router()


@dataclass(frozen=True)
class SearchSession:
    session_id: str
    user_id: int
    chat_id: int
    query: str
    title: str | None
    page: int
    results: list


@dataclass(frozen=True)
class AuthorSession:
    session_id: str
    user_id: int
    chat_id: int
    query: str
    page: int
    authors: list[AuthorResult]


search_sessions: dict[str, SearchSession] = {}
author_sessions: dict[str, AuthorSession] = {}

flibusta = FlibustaClient(
    settings.base_url,
    timeout=settings.request_timeout_seconds,
    proxy=settings.http_proxy,
    retries=settings.flibusta_retries,
    retry_delay=settings.flibusta_retry_delay_seconds,
    max_redirects=settings.flibusta_max_redirects,
)


@router.message(Command("start"))
async def start(message: Message) -> None:
    log_user_action(message.from_user, message.chat.id, "start")
    await telegram_retry(
        lambda: message.answer(
            "Отправь название книги или автора. Еще можно использовать /search &lt;запрос&gt; "
            "или /author &lt;автор&gt;."
        )
    )


@router.message(Command("search"))
async def search_command(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        log_user_action(message.from_user, message.chat.id, "search_empty")
        await telegram_retry(
            lambda: message.answer("Напиши запрос после команды: /search мастер и маргарита")
        )
        return
    await send_search_results(message, query)


@router.message(Command("author"))
async def author_command(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        log_user_action(message.from_user, message.chat.id, "author_search_empty")
        await telegram_retry(
            lambda: message.answer("Напиши автора после команды: /author сапковский")
        )
        return
    await send_author_results(message, query)


@router.message(F.text)
async def search_text(message: Message) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    await send_search_results(message, text)


@router.callback_query(F.data.startswith("book:"))
async def show_book(callback: CallbackQuery) -> None:
    book_id = callback.data.split(":", 1)[1]
    started_at = monotonic()
    log_user_action(callback.from_user, callback.message.chat.id, "book_open", book_id=book_id)
    await callback_answer(callback)

    try:
        details = await flibusta.details(book_id)
    except FlibustaError as exc:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "book_open_failed",
            book_id=book_id,
            error=str(exc),
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: callback.message.answer(str(exc)))
        return

    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "book_open_ok",
        book_id=book_id,
        title=details.title,
        formats=",".join(item.code for item in details.formats) or "none",
        duration=elapsed(started_at),
    )
    await telegram_retry(
        lambda: callback.message.answer(_book_text(details), reply_markup=_formats_keyboard(details))
    )


@router.callback_query(F.data.startswith("bauthor:"))
async def show_book_author_books(callback: CallbackQuery) -> None:
    author_id = callback.data.split(":", 1)[1]
    await callback_answer(callback)

    started_at = monotonic()
    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "book_author_open",
        author_id=author_id,
    )
    try:
        author_name, books = await flibusta.author_books(author_id, limit=settings.search_results_limit)
    except FlibustaError as exc:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "book_author_open_failed",
            author_id=author_id,
            error=str(exc),
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: callback.message.answer(str(exc)))
        return

    if not books:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "book_author_open_empty",
            author_id=author_id,
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: callback.message.answer("У этого автора книги не найдены."))
        return

    book_session = _create_search_session(
        callback.from_user.id,
        callback.message.chat.id,
        f"author:{author_name}",
        books,
        title=f"Книги автора: <b>{escape(author_name)}</b>",
    )
    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "book_author_open_ok",
        author_id=author_id,
        author=author_name,
        books=len(books),
        pages=total_pages(len(books)),
        duration=elapsed(started_at),
    )
    await telegram_retry(
        lambda: callback.message.answer(
            _search_results_text(book_session),
            reply_markup=_search_results_keyboard(book_session),
        )
    )


@router.callback_query(F.data.startswith("page:"))
async def paginate_search(callback: CallbackQuery) -> None:
    _, session_id, page_raw = callback.data.split(":", 2)

    session = search_sessions.get(session_id)
    if session is None:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Поиск устарел. Запусти его еще раз."))
        return

    if callback.from_user.id != session.user_id or callback.message.chat.id != session.chat_id:
        await telegram_retry(lambda: callback.answer("Это не твоя выдача.", show_alert=True))
        return

    try:
        page = int(page_raw)
    except ValueError:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Некорректная страница выдачи."))
        return

    page_count = total_pages(len(session.results))
    if page < 0 or page >= page_count:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Такой страницы выдачи нет."))
        return

    updated = SearchSession(
        session_id=session.session_id,
        user_id=session.user_id,
        chat_id=session.chat_id,
        query=session.query,
        title=session.title,
        page=page,
        results=session.results,
    )
    search_sessions[session_id] = updated
    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "search_page",
        query=session.query,
        page=page + 1,
        total_pages=page_count,
    )
    await callback_answer(callback)
    await telegram_retry(
        lambda: callback.message.edit_text(
            _search_results_text(updated),
            reply_markup=_search_results_keyboard(updated),
        )
    )


@router.callback_query(F.data.startswith("apage:"))
async def paginate_authors(callback: CallbackQuery) -> None:
    _, session_id, page_raw = callback.data.split(":", 2)

    session = author_sessions.get(session_id)
    if session is None:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Поиск авторов устарел. Запусти его еще раз."))
        return

    if callback.from_user.id != session.user_id or callback.message.chat.id != session.chat_id:
        await telegram_retry(lambda: callback.answer("Это не твоя выдача.", show_alert=True))
        return

    try:
        page = int(page_raw)
    except ValueError:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Некорректная страница авторов."))
        return

    page_count = total_pages(len(session.authors))
    if page < 0 or page >= page_count:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Такой страницы авторов нет."))
        return

    updated = AuthorSession(
        session_id=session.session_id,
        user_id=session.user_id,
        chat_id=session.chat_id,
        query=session.query,
        page=page,
        authors=session.authors,
    )
    author_sessions[session_id] = updated
    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "author_search_page",
        query=session.query,
        page=page + 1,
        total_pages=page_count,
    )
    await callback_answer(callback)
    await telegram_retry(
        lambda: callback.message.edit_text(
            _author_results_text(updated),
            reply_markup=_author_results_keyboard(updated),
        )
    )


@router.callback_query(F.data.startswith("author:"))
async def show_author_books(callback: CallbackQuery) -> None:
    _, session_id, author_id = callback.data.split(":", 2)
    await callback_answer(callback)

    author_session = author_sessions.get(session_id)
    if author_session is None:
        await telegram_retry(lambda: callback.message.answer("Поиск авторов устарел. Запусти его еще раз."))
        return

    if callback.from_user.id != author_session.user_id or callback.message.chat.id != author_session.chat_id:
        await telegram_retry(lambda: callback.answer("Это не твоя выдача.", show_alert=True))
        return

    started_at = monotonic()
    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "author_open",
        query=author_session.query,
        author_id=author_id,
    )
    try:
        author_name, books = await flibusta.author_books(author_id, limit=settings.search_results_limit)
    except FlibustaError as exc:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "author_open_failed",
            author_id=author_id,
            error=str(exc),
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: callback.message.answer(str(exc)))
        return

    if not books:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "author_open_empty",
            author_id=author_id,
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: callback.message.answer("У этого автора книги не найдены."))
        return

    book_session = _create_search_session(
        callback.from_user.id,
        callback.message.chat.id,
        f"author:{author_name}",
        books,
        title=f"Книги автора: <b>{escape(author_name)}</b>",
    )
    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "author_open_ok",
        author_id=author_id,
        author=author_name,
        books=len(books),
        pages=total_pages(len(books)),
        duration=elapsed(started_at),
    )
    await telegram_retry(
        lambda: callback.message.answer(
            _search_results_text(book_session),
            reply_markup=_search_results_keyboard(book_session),
        )
    )


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback_answer(callback)


@router.callback_query(F.data.startswith("dl:"))
async def download_book(callback: CallbackQuery) -> None:
    _, book_id, fmt = callback.data.split(":", 2)
    started_at = monotonic()
    log_user_action(callback.from_user, callback.message.chat.id, "download_start", book_id=book_id, fmt=fmt)
    await callback_answer(callback, "Скачиваю...")
    status_message = await telegram_retry(
        lambda: callback.message.answer(f"Скачиваю {escape(fmt.upper())}...")
    )

    try:
        details = await flibusta.details(book_id)
        target = next((item for item in details.formats if item.code == fmt), None)
        if target is None:
            log_user_action(
                callback.from_user,
                callback.message.chat.id,
                "download_format_missing",
                book_id=book_id,
                fmt=fmt,
                duration=elapsed(started_at),
            )
            await telegram_retry(
                lambda: callback.message.answer("Этот формат больше не найден на странице книги.")
            )
            return

        max_bytes = settings.max_download_mb * 1024 * 1024
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "flibusta_download_start",
            book_id=book_id,
            fmt=fmt,
            url=target.url,
        )
        content, filename, _content_type = await flibusta.download(target.url, max_bytes=max_bytes)
    except FlibustaError as exc:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "download_failed",
            book_id=book_id,
            fmt=fmt,
            error=str(exc),
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: callback.message.answer(str(exc)))
        return

    size_mb = len(content) / 1024 / 1024
    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "flibusta_download_ok",
        book_id=book_id,
        fmt=fmt,
        filename=filename,
        size_mb=f"{size_mb:.2f}",
        duration=elapsed(started_at),
    )
    if isinstance(status_message, Message):
        await telegram_retry(
            lambda: status_message.edit_text(f"Файл скачан ({size_mb:.1f} МБ), отправляю в Telegram...")
        )
    else:
        await telegram_retry(
            lambda: callback.message.answer(f"Файл скачан ({size_mb:.1f} МБ), отправляю в Telegram...")
        )

    sent = await telegram_retry(
        lambda: callback.message.answer_document(BufferedInputFile(content, filename=filename)),
        attempts=4,
    )
    if sent is None:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "telegram_upload_failed",
            book_id=book_id,
            fmt=fmt,
            filename=filename,
            duration=elapsed(started_at),
        )
        await telegram_retry(
            lambda: callback.message.answer("Не удалось отправить файл в Telegram через текущий proxy.")
        )
        return

    log_user_action(
        callback.from_user,
        callback.message.chat.id,
        "download_ok",
        book_id=book_id,
        fmt=fmt,
        filename=filename,
        size_mb=f"{size_mb:.2f}",
        duration=elapsed(started_at),
    )


async def send_search_results(message: Message, query: str) -> None:
    started_at = monotonic()
    log_user_action(message.from_user, message.chat.id, "search_start", query=query)
    try:
        await telegram_retry(
            lambda: message.bot.send_chat_action(message.chat.id, ChatAction.TYPING),
            attempts=2,
        )
    except TelegramNetworkError:
        logger.warning("Could not send typing action")

    try:
        results = await flibusta.search(query, limit=settings.search_results_limit)
    except FlibustaError as exc:
        log_user_action(
            message.from_user,
            message.chat.id,
            "search_failed",
            query=query,
            error=str(exc),
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: message.answer(str(exc)))
        return

    if not results:
        log_user_action(
            message.from_user,
            message.chat.id,
            "search_empty_result",
            query=query,
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: message.answer("Ничего не найдено."))
        return

    session = _create_search_session(message.from_user.id, message.chat.id, query, results)

    log_user_action(
        message.from_user,
        message.chat.id,
        "search_ok",
        query=query,
        results=len(results),
        pages=total_pages(len(results)),
        duration=elapsed(started_at),
    )
    await telegram_retry(
        lambda: message.answer(
            _search_results_text(session),
            reply_markup=_search_results_keyboard(session),
        )
    )


async def send_author_results(message: Message, query: str) -> None:
    started_at = monotonic()
    log_user_action(message.from_user, message.chat.id, "author_search_start", query=query)
    try:
        await telegram_retry(
            lambda: message.bot.send_chat_action(message.chat.id, ChatAction.TYPING),
            attempts=2,
        )
    except TelegramNetworkError:
        logger.warning("Could not send typing action")

    try:
        authors = await flibusta.search_authors(query, limit=settings.search_results_limit)
    except FlibustaError as exc:
        log_user_action(
            message.from_user,
            message.chat.id,
            "author_search_failed",
            query=query,
            error=str(exc),
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: message.answer(str(exc)))
        return

    if not authors:
        log_user_action(
            message.from_user,
            message.chat.id,
            "author_search_empty_result",
            query=query,
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: message.answer("Авторы не найдены."))
        return

    session = _create_author_session(message.from_user.id, message.chat.id, query, authors)
    log_user_action(
        message.from_user,
        message.chat.id,
        "author_search_ok",
        query=query,
        authors=len(authors),
        pages=total_pages(len(authors)),
        duration=elapsed(started_at),
    )
    await telegram_retry(
        lambda: message.answer(
            _author_results_text(session),
            reply_markup=_author_results_keyboard(session),
        )
    )


async def telegram_retry(
    call: Callable[[], Awaitable[T]],
    attempts: int = 3,
    delay: float = 2,
) -> T | None:
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TelegramNetworkError:
            if attempt == attempts:
                logger.exception("Telegram request failed after %d attempts", attempts)
                return None
            logger.warning("Telegram request failed, retrying (%d/%d)", attempt, attempts)
            await sleep(delay * attempt)

    return None


async def callback_answer(callback: CallbackQuery, text: str | None = None) -> None:
    try:
        await telegram_retry(lambda: callback.answer(text))
    except TelegramBadRequest as exc:
        message = str(exc)
        if "query is too old" in message or "query ID is invalid" in message:
            logger.info("Callback answer skipped: %s", message)
            return
        raise


def elapsed(started_at: float) -> str:
    return f"{monotonic() - started_at:.2f}s"


def log_user_action(user: User | None, chat_id: int, action: str, **fields: object) -> None:
    user_id = user.id if user else None
    username = user.username if user else None
    payload = " ".join(f"{key}={short_log_value(value)}" for key, value in fields.items())
    if payload:
        logger.info("action=%s user_id=%s username=%s chat_id=%s %s", action, user_id, username, chat_id, payload)
        return
    logger.info("action=%s user_id=%s username=%s chat_id=%s", action, user_id, username, chat_id)


def short_log_value(value: object, limit: int = 160) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _create_search_session(
    user_id: int,
    chat_id: int,
    query: str,
    results: list,
    title: str | None = None,
) -> SearchSession:
    _prune_sessions(search_sessions)

    session = SearchSession(
        session_id=uuid4().hex[:10],
        user_id=user_id,
        chat_id=chat_id,
        query=query,
        title=title,
        page=0,
        results=results,
    )
    search_sessions[session.session_id] = session
    return session


def _create_author_session(user_id: int, chat_id: int, query: str, authors: list[AuthorResult]) -> AuthorSession:
    _prune_sessions(author_sessions)
    session = AuthorSession(
        session_id=uuid4().hex[:10],
        user_id=user_id,
        chat_id=chat_id,
        query=query,
        page=0,
        authors=authors,
    )
    author_sessions[session.session_id] = session
    return session


def _prune_sessions(storage: dict[str, object]) -> None:
    if len(storage) > 100:
        stale_keys = list(storage.keys())[:20]
        for key in stale_keys:
            storage.pop(key, None)


def _search_results_text(session: SearchSession, title: str | None = None) -> str:
    total_results = len(session.results)
    page_count = total_pages(total_results)
    start = session.page * SEARCH_PAGE_SIZE + 1
    end = min(total_results, (session.page + 1) * SEARCH_PAGE_SIZE)
    heading = title or session.title or f"Нашел варианты по запросу: <b>{escape(session.query)}</b>"
    return (
        f"{heading}\n"
        f"Показаны {start}-{end} из {total_results}. Страница {session.page + 1}/{page_count}."
    )


def _search_results_keyboard(session: SearchSession):
    keyboard = InlineKeyboardBuilder()
    items = page_items(session.results, session.page)
    for item in items:
        label = item.title if not item.author else f"{item.title} - {item.author}"
        keyboard.row(InlineKeyboardButton(text=label[:64], callback_data=f"book:{item.book_id}"))

    page_count = total_pages(len(session.results))
    if page_count > 1:
        nav_buttons: list[InlineKeyboardButton] = []
        if session.page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="<< Назад",
                    callback_data=f"page:{session.session_id}:{session.page - 1}",
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{session.page + 1}/{page_count}",
                callback_data="noop",
            )
        )
        if session.page < page_count - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Еще >>",
                    callback_data=f"page:{session.session_id}:{session.page + 1}",
                )
            )
        keyboard.row(*nav_buttons)

    return keyboard.as_markup()


def _author_results_text(session: AuthorSession) -> str:
    total_results = len(session.authors)
    page_count = total_pages(total_results)
    start = session.page * SEARCH_PAGE_SIZE + 1
    end = min(total_results, (session.page + 1) * SEARCH_PAGE_SIZE)
    return (
        f"Нашел авторов по запросу: <b>{escape(session.query)}</b>\n"
        f"Показаны {start}-{end} из {total_results}. Страница {session.page + 1}/{page_count}."
    )


def _author_results_keyboard(session: AuthorSession):
    keyboard = InlineKeyboardBuilder()
    items = page_items(session.authors, session.page)
    for item in items:
        keyboard.row(
            InlineKeyboardButton(
                text=item.name[:64],
                callback_data=f"author:{session.session_id}:{item.author_id}",
            )
        )

    page_count = total_pages(len(session.authors))
    if page_count > 1:
        nav_buttons: list[InlineKeyboardButton] = []
        if session.page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="<< Назад",
                    callback_data=f"apage:{session.session_id}:{session.page - 1}",
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{session.page + 1}/{page_count}",
                callback_data="noop",
            )
        )
        if session.page < page_count - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Еще >>",
                    callback_data=f"apage:{session.session_id}:{session.page + 1}",
                )
            )
        keyboard.row(*nav_buttons)

    return keyboard.as_markup()


def log_startup_config() -> None:
    logger.info(
        "startup flibusta_base_url=%s request_timeout=%ss flibusta_retries=%s "
        "flibusta_retry_delay=%ss flibusta_max_redirects=%s max_download_mb=%s",
        settings.base_url,
        settings.request_timeout_seconds,
        settings.flibusta_retries,
        settings.flibusta_retry_delay_seconds,
        settings.flibusta_max_redirects,
        settings.max_download_mb,
    )
    logger.info(
        "startup telegram_timeout=%ss polling_retry_delay=%ss telegram_proxy=%s flibusta_proxy=%s",
        settings.telegram_request_timeout_seconds,
        settings.polling_retry_delay_seconds,
        safe_proxy_info(settings.telegram_proxy),
        safe_proxy_info(settings.http_proxy),
    )


def safe_proxy_info(proxy: str | None) -> str:
    if not proxy:
        return "disabled"

    parsed = urlparse(proxy)
    scheme = parsed.scheme or "unknown"
    host = parsed.hostname or "unknown"
    if parsed.port is None:
        return f"enabled scheme={scheme} host={host}"
    return f"enabled scheme={scheme} host={host} port={parsed.port}"


def _book_text(details: BookDetails) -> str:
    parts = [f"<b>{escape(details.title)}</b>"]
    if details.authors:
        parts.append(escape(", ".join(details.authors[:5])))

    meta_parts: list[str] = []
    if details.genres:
        meta_parts.append(", ".join(details.genres[:3]))
    if details.file_size:
        meta_parts.append(details.file_size)
    if details.pages:
        meta_parts.append(f"{details.pages} с.")
    if meta_parts:
        parts.append(escape(" · ".join(meta_parts)))

    if details.annotation:
        parts.append(escape(details.annotation))
    if not details.formats:
        parts.append("Доступные форматы не найдены.")
    return "\n\n".join(parts)


def _formats_keyboard(details: BookDetails):
    author_buttons = [item for item in details.author_refs[:3] if item.author_id]
    if not details.formats and not author_buttons:
        return None

    keyboard = InlineKeyboardBuilder()
    for item in author_buttons:
        keyboard.row(
            InlineKeyboardButton(
                text=f"Автор: {item.name[:48]}",
                callback_data=f"bauthor:{item.author_id}",
            )
        )

    format_row: list[InlineKeyboardButton] = []
    for item in details.formats:
        format_row.append(InlineKeyboardButton(text=item.label, callback_data=f"dl:{details.book_id}:{item.code}"))
        if len(format_row) == 3:
            keyboard.row(*format_row)
            format_row = []

    if format_row:
        keyboard.row(*format_row)

    return keyboard.as_markup()


async def main() -> None:
    log_startup_config()
    session = AiohttpSession(
        proxy=settings.telegram_proxy,
        timeout=settings.telegram_request_timeout_seconds,
    )
    bot = Bot(
        settings.telegram_bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        while True:
            try:
                await dispatcher.start_polling(
                    bot,
                    polling_timeout=int(settings.telegram_request_timeout_seconds),
                )
                break
            except TelegramNetworkError:
                logger.exception(
                    "Telegram API is unavailable; retrying in %.0f seconds",
                    settings.polling_retry_delay_seconds,
                )
                await sleep(settings.polling_retry_delay_seconds)
    finally:
        await flibusta.close()
        await bot.session.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
