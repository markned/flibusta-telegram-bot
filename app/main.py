from __future__ import annotations

import logging
import json
import re
from asyncio import sleep
from dataclasses import dataclass
from html import escape
from pathlib import Path
from time import monotonic
from typing import Awaitable, Callable, TypeVar
from urllib.parse import urlparse
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge, TelegramNetworkError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.types import User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Settings
from app.flibusta import AuthorResult, BookDetails, FlibustaClient, FlibustaError
from app.handlers.kindle import build_kindle_router, user_message_for_exception
from app.pagination import SEARCH_PAGE_SIZE, page_items, total_pages
from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.services.email_sender import EmailSender
from app.services.conversion import ConversionService
from app.services.kindle import KindleService
from app.services.kindle_queue import KindleQueue

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
if settings.log_level.upper() != "DEBUG":
    logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
T = TypeVar("T")
BOOK_SEARCH_BUTTON = "📚 Поиск книги"
AUTHOR_SEARCH_BUTTON = "👤 Поиск автора"
HELP_BUTTON = "ℹ️ Как пользоваться"
BOOK_MODE = "book"
AUTHOR_MODE = "author"
SMART_MODE = "smart"
PREFS_PATH = Path("user_prefs.json")

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
user_search_modes: dict[int, str] = {}

flibusta = FlibustaClient(
    settings.base_url,
    timeout=settings.request_timeout_seconds,
    proxy=settings.normalized_http_proxy,
    retries=settings.flibusta_retries,
    retry_delay=settings.flibusta_retry_delay_seconds,
    max_redirects=settings.flibusta_max_redirects,
)
db = Database(settings.database_path)
kindle_settings_repo = KindleSettingsRepository(db)
kindle_deliveries_repo = KindleDeliveriesRepository(db)
email_sender = EmailSender(
    host=settings.smtp_host,
    port=settings.smtp_port,
    username=settings.smtp_username,
    password=settings.smtp_password,
    from_email=settings.smtp_from_email,
    starttls=settings.smtp_starttls,
)
kindle_service = KindleService(
    flibusta=flibusta,
    settings_repo=kindle_settings_repo,
    deliveries_repo=kindle_deliveries_repo,
    email_sender=email_sender,
    conversion_service=ConversionService(),
    max_attachment_bytes=settings.kindle_max_attachment_mb * 1024 * 1024,
    default_format=settings.kindle_default_format,
    send_rate_limit_per_hour=settings.kindle_send_rate_limit_per_hour,
    enable_conversion=settings.kindle_enable_conversion,
    conversion_target_format=settings.kindle_conversion_target_format,
)
kindle_queue = KindleQueue(
    service=kindle_service,
    worker_concurrency=settings.kindle_worker_concurrency,
    user_concurrency=settings.kindle_user_concurrency,
    error_message_for_exception=user_message_for_exception,
)


@router.message(Command("start"))
async def start(message: Message) -> None:
    log_user_action(message.from_user, message.chat.id, "start")
    if message.from_user:
        user_search_modes[message.from_user.id] = SMART_MODE
    await telegram_retry(
        lambda: message.answer(
            "Что ищем?\n\n"
            "Можно просто написать запрос — я сам попробую понять, это книга или автор.\n"
            "Если хочешь явно выбрать режим, нажми <b>📚 Поиск книги</b> или "
            "<b>👤 Поиск автора</b>.\n\n"
            "Кнопки снизу всегда доступны.",
            reply_markup=main_reply_keyboard(),
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


@router.message(Command("mode"))
async def mode_command(message: Message, command: CommandObject) -> None:
    mode = (command.args or "").strip().lower()
    if mode in {"book", "books", "книга", "книги"}:
        if message.from_user:
            user_search_modes[message.from_user.id] = BOOK_MODE
        await telegram_retry(lambda: message.answer("Режим: поиск книги.", reply_markup=main_reply_keyboard()))
        return

    if mode in {"author", "authors", "автор", "авторы"}:
        if message.from_user:
            user_search_modes[message.from_user.id] = AUTHOR_MODE
        await telegram_retry(lambda: message.answer("Режим: поиск автора.", reply_markup=main_reply_keyboard()))
        return

    current_mode = user_search_modes.get(message.from_user.id if message.from_user else 0, BOOK_MODE)
    current_text = "поиск автора" if current_mode == AUTHOR_MODE else "поиск книги"
    await telegram_retry(
        lambda: message.answer(
            f"Сейчас: {current_text}.\n"
            "Используй /mode book или /mode author.",
            reply_markup=main_reply_keyboard(),
        )
    )


@router.message(F.text)
async def search_text(message: Message) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return

    if text == BOOK_SEARCH_BUTTON:
        if message.from_user:
            user_search_modes[message.from_user.id] = BOOK_MODE
        await telegram_retry(
            lambda: message.answer("Напиши название книги.", reply_markup=main_reply_keyboard())
        )
        return

    if text == AUTHOR_SEARCH_BUTTON:
        if message.from_user:
            user_search_modes[message.from_user.id] = AUTHOR_MODE
        await telegram_retry(lambda: message.answer("Напиши имя автора.", reply_markup=main_reply_keyboard()))
        return

    if text == HELP_BUTTON:
        await telegram_retry(
            lambda: message.answer(
                "1. Выбери, что ищешь: книгу или автора.\n"
                "2. Или просто напиши запрос — я попробую понять сам.\n"
                "3. Открой карточку и выбери формат для скачивания.\n\n"
                "Команды тоже работают: /search и /author.",
                reply_markup=main_reply_keyboard(),
            )
        )
        return

    mode = user_search_modes.get(message.from_user.id if message.from_user else 0, SMART_MODE)
    if mode == AUTHOR_MODE:
        await send_author_results(message, text)
        return
    if mode == SMART_MODE:
        await send_smart_results(message, text)
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
        lambda: callback.message.answer(
            _book_text(details),
            reply_markup=_formats_keyboard(details, preferred_format=_preferred_format(callback.from_user.id)),
        )
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

        max_bytes = min(settings.max_download_mb, settings.telegram_max_upload_mb) * 1024 * 1024
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
    if size_mb > settings.telegram_max_upload_mb:
        log_user_action(
            callback.from_user,
            callback.message.chat.id,
            "telegram_upload_too_large",
            book_id=book_id,
            fmt=fmt,
            filename=filename,
            size_mb=f"{size_mb:.2f}",
            telegram_max_upload_mb=settings.telegram_max_upload_mb,
            duration=elapsed(started_at),
        )
        await telegram_retry(
            lambda: callback.message.answer(
                f"Файл {size_mb:.1f} МБ больше лимита отправки Telegram "
                f"({settings.telegram_max_upload_mb} МБ)."
            )
        )
        return

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
            lambda: callback.message.answer(
                "Не удалось отправить файл в Telegram. Если файл большой, попробуй другой формат."
            )
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
    _remember_preferred_format(callback.from_user.id, fmt)


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
        results = _rank_and_dedupe_books(
            await flibusta.search(_clean_query(query), limit=settings.search_results_limit),
            query,
        )
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
        authors = _rank_authors(
            await flibusta.search_authors(_clean_query(query), limit=settings.search_results_limit),
            query,
        )
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


async def send_smart_results(message: Message, query: str) -> None:
    started_at = monotonic()
    cleaned = _clean_query(query)
    log_user_action(message.from_user, message.chat.id, "smart_search_start", query=query)
    try:
        await telegram_retry(
            lambda: message.bot.send_chat_action(message.chat.id, ChatAction.TYPING),
            attempts=2,
        )
        used_query = cleaned
        raw_books, raw_authors = await flibusta.search_all(
            cleaned,
            book_limit=settings.search_results_limit,
            author_limit=settings.search_results_limit,
        )
        if not raw_books and not raw_authors:
            for fallback_query in _fallback_queries(cleaned):
                raw_books, raw_authors = await flibusta.search_all(
                    fallback_query,
                    book_limit=settings.search_results_limit,
                    author_limit=settings.search_results_limit,
                )
                if raw_books or raw_authors:
                    used_query = fallback_query
                    break
    except FlibustaError as exc:
        log_user_action(
            message.from_user,
            message.chat.id,
            "smart_search_failed",
            query=query,
            error=str(exc),
            duration=elapsed(started_at),
        )
        await telegram_retry(lambda: message.answer(str(exc)))
        return

    books = _rank_and_dedupe_books(raw_books, query)
    authors = _rank_authors(raw_authors, query)
    top_author_is_exact = bool(authors and _norm(authors[0].name) == _norm(query))
    top_book_is_exact = bool(books and _norm(_base_title(books[0].title)) == _norm(query))

    if top_author_is_exact and not top_book_is_exact:
        session = _create_author_session(message.from_user.id, message.chat.id, used_query, authors)
        log_user_action(
            message.from_user,
            message.chat.id,
            "smart_search_author_guess",
            query=query,
            authors=len(authors),
            books=len(books),
            duration=elapsed(started_at),
        )
        await telegram_retry(
            lambda: message.answer(
                f"Похоже, это автор. Нашёл по запросу: <b>{escape(used_query)}</b>",
                reply_markup=_author_results_keyboard(session),
            )
        )
        return

    if books:
        title = None
        if used_query != cleaned:
            title = f"Точного совпадения не нашёл. Ближайшее по запросу: <b>{escape(used_query)}</b>"
        session = _create_search_session(message.from_user.id, message.chat.id, used_query, books, title=title)
        log_user_action(
            message.from_user,
            message.chat.id,
            "smart_search_books",
            query=query,
            books=len(books),
            authors=len(authors),
            duration=elapsed(started_at),
        )
        await telegram_retry(
            lambda: message.answer(
                _search_results_text(session),
                reply_markup=_search_results_keyboard(session),
            )
        )
        return

    if authors:
        session = _create_author_session(message.from_user.id, message.chat.id, used_query, authors)
        await telegram_retry(
            lambda: message.answer(
                f"Книг не нашёл, но нашёл авторов по запросу: <b>{escape(used_query)}</b>",
                reply_markup=_author_results_keyboard(session),
            )
        )
        return

    await telegram_retry(lambda: message.answer("Ничего не найдено."))


async def telegram_retry(
    call: Callable[[], Awaitable[T]],
    attempts: int = 3,
    delay: float = 2,
) -> T | None:
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TelegramEntityTooLarge:
            logger.exception("Telegram rejected upload: request entity too large")
            return None
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
        "flibusta_retry_delay=%ss flibusta_max_redirects=%s max_download_mb=%s telegram_max_upload_mb=%s",
        settings.base_url,
        settings.request_timeout_seconds,
        settings.flibusta_retries,
        settings.flibusta_retry_delay_seconds,
        settings.flibusta_max_redirects,
        settings.max_download_mb,
        settings.telegram_max_upload_mb,
    )
    logger.info(
        "startup telegram_timeout=%ss polling_retry_delay=%ss telegram_proxy=%s flibusta_proxy=%s",
        settings.telegram_request_timeout_seconds,
        settings.polling_retry_delay_seconds,
        safe_proxy_info(settings.telegram_proxy),
        safe_proxy_info(settings.http_proxy),
    )
    logger.info(
        "startup smtp_provider=%s smtp_host=%s smtp_port=%s smtp_from=%s",
        settings.smtp_provider,
        settings.smtp_host or "disabled",
        settings.smtp_port,
        _mask_sender(settings.smtp_from_email),
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


def _mask_sender(value: str | None) -> str:
    if not value or "@" not in value:
        return "disabled"
    local, domain = value.split("@", 1)
    return f"{local[:1]}***@{domain}"


def _smtp_config_present() -> bool:
    try:
        email_sender.validate_config()
    except Exception:
        return False
    return True


def _book_text(details: BookDetails) -> str:
    parts = [f"<b>{escape(details.title)}</b>"]
    if details.authors:
        parts.append(escape(", ".join(details.authors[:5])))
    if details.translators:
        parts.append(f"Перевод: {escape(', '.join(details.translators[:5]))}")
    if details.illustrators:
        parts.append(f"Иллюстрации: {escape(', '.join(details.illustrators[:5]))}")

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


def _formats_keyboard(details: BookDetails, preferred_format: str | None = None):
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
    formats = sorted(details.formats, key=lambda item: item.code != preferred_format)
    for item in formats:
        label = f"⭐ {item.label}" if item.code == preferred_format else item.label
        format_row.append(InlineKeyboardButton(text=label, callback_data=f"dl:{details.book_id}:{item.code}"))
        if len(format_row) == 3:
            keyboard.row(*format_row)
            format_row = []

    if format_row:
        keyboard.row(*format_row)
    keyboard.row(InlineKeyboardButton(text="📤 Send to Kindle", callback_data=f"kindle:{details.book_id}"))

    return keyboard.as_markup()


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BOOK_SEARCH_BUTTON),
                KeyboardButton(text=AUTHOR_SEARCH_BUTTON),
            ],
            [KeyboardButton(text=HELP_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Название книги или автор",
    )


def _load_prefs() -> dict[str, dict[str, str]]:
    if not PREFS_PATH.exists():
        return {}
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_prefs(data: dict[str, dict[str, str]]) -> None:
    PREFS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _preferred_format(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return _load_prefs().get(str(user_id), {}).get("preferred_format")


def _remember_preferred_format(user_id: int | None, fmt: str) -> None:
    if user_id is None:
        return
    data = _load_prefs()
    data[str(user_id)] = {"preferred_format": fmt}
    _save_prefs(data)


def _clean_query(query: str) -> str:
    cleaned = query.replace("ё", "е").replace("Ё", "Е")
    cleaned = re.sub(r"[«»\"“”„]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _norm(text: str) -> str:
    text = _clean_query(text).lower()
    text = re.sub(r"\[[^\]]+\]|\([^)]*\)", "", text)
    text = re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _base_title(title: str) -> str:
    return re.sub(r"\s*(\[[^\]]+\]|\([^)]*\))", "", title).strip()


def _rank_and_dedupe_books(results: list, query: str) -> list:
    q = _norm(query)
    deduped = {}
    for item in results:
        key = (_norm(_base_title(item.title)), _norm(item.author or ""))
        current = deduped.get(key)
        if current is None or _book_score(item, q) > _book_score(current, q):
            deduped[key] = item
    return sorted(deduped.values(), key=lambda item: _book_score(item, q), reverse=True)


def _book_score(item, q: str) -> tuple[int, int, int]:
    title = _norm(_base_title(item.title))
    full = _norm(item.title)
    return (
        int(title == q),
        int(title.startswith(q)),
        int(q in full),
    )


def _rank_authors(authors: list[AuthorResult], query: str) -> list[AuthorResult]:
    q = _norm(query)
    return sorted(authors, key=lambda item: (_norm(item.name) == q, q in _norm(item.name)), reverse=True)


def _fallback_queries(query: str) -> list[str]:
    words = [word for word in re.split(r"\s+", query) if word]
    candidates = []
    for size in (4, 3, 2, 1):
        if len(words) >= size:
            candidate = " ".join(words[:size])
            if candidate != query and candidate not in candidates:
                candidates.append(candidate)
    return candidates


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="search", description="поиск книг"),
            BotCommand(command="author", description="поиск авторов"),
            BotCommand(command="mode", description="переключить режим"),
            BotCommand(command="kindle_email", description="сохранить Kindle e-mail"),
            BotCommand(command="kindle_help", description="настройка Send to Kindle"),
            BotCommand(command="kindle_status", description="статус Kindle"),
            BotCommand(command="kindle_remove", description="удалить Kindle e-mail"),
            BotCommand(command="kindle_history", description="история Kindle"),
            BotCommand(command="kindle_format", description="формат Kindle"),
            BotCommand(command="start", description="открыть меню"),
        ]
    )


async def main() -> None:
    log_startup_config()
    await db.initialize()
    interrupted = await kindle_deliveries_repo.mark_interrupted_inflight_failed()
    if interrupted:
        logger.warning("Marked interrupted Kindle deliveries as failed: count=%s", interrupted)
    session = AiohttpSession(
        proxy=settings.normalized_telegram_proxy,
        timeout=settings.telegram_request_timeout_seconds,
    )
    bot = Bot(
        settings.telegram_bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.include_router(
        build_kindle_router(
            db=db,
            settings_repo=kindle_settings_repo,
            deliveries_repo=kindle_deliveries_repo,
            kindle_queue=kindle_queue,
            smtp_from_email=settings.smtp_from_email,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_config_present=_smtp_config_present(),
            default_format=settings.kindle_default_format,
            max_attachment_mb=settings.kindle_max_attachment_mb,
            admin_user_ids=settings.admin_ids,
        )
    )
    try:
        await kindle_queue.start(bot)
        await setup_bot_commands(bot)
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
        await kindle_queue.stop()
        await flibusta.close()
        await bot.session.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
