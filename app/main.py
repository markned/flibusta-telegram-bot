from __future__ import annotations

import logging
import re
from asyncio import sleep
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from time import monotonic, time
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
from app.handlers.admin import build_admin_router
from app.pagination import SEARCH_PAGE_SIZE, page_items, total_pages
from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.repositories.user_preferences import UserPreferencesRepository
from app.repositories.cache import CacheRepository
from app.repositories.favorites import FavoritesRepository
from app.repositories.download_history import DownloadHistoryRepository, DownloadHistoryItem
from app.repositories.last_books import LastBooksRepository
from app.repositories.access import AccessRepository
from app.services.email_sender import EmailSender
from app.services.conversion import ConversionService
from app.services.kindle import KindleService
from app.services.kindle_queue import KindleQueue
from app.services.cached_flibusta import CachedFlibustaClient
from app.services.query_analyzer import analyze_query
from app.services.ai_assistant import AiAssistant
from app.middlewares.access import AccessMiddleware

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
if settings.log_level.upper() != "DEBUG":
    logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
T = TypeVar("T")
FAVORITES_BUTTON = "⭐ Избранное"
HISTORY_BUTTON = "🕘 История"
LAST_BUTTON = "📚 Последняя"
KINDLE_BUTTON = "⚙️ Kindle"

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
retry_sessions: dict[str, str] = {}
search_timestamps: dict[int, list[float]] = {}

raw_flibusta = FlibustaClient(
    settings.base_url,
    timeout=settings.request_timeout_seconds,
    proxy=settings.normalized_http_proxy,
    retries=settings.flibusta_retries,
    retry_delay=settings.flibusta_retry_delay_seconds,
    max_redirects=settings.flibusta_max_redirects,
)
db = Database(settings.database_path)
cache_repo = CacheRepository(db)
favorites_repo = FavoritesRepository(db)
download_history_repo = DownloadHistoryRepository(db)
last_books_repo = LastBooksRepository(db)
access_repo = AccessRepository(db)
flibusta = CachedFlibustaClient(raw_flibusta, cache_repo, enabled=settings.cache_enabled, ttls={
    "book_search": settings.cache_book_search_ttl_seconds,
    "author_search": settings.cache_author_search_ttl_seconds,
    "smart_search": settings.cache_smart_search_ttl_seconds,
    "book_details": settings.cache_book_details_ttl_seconds,
    "author_books": settings.cache_author_books_ttl_seconds,
})
kindle_settings_repo = KindleSettingsRepository(db)
kindle_deliveries_repo = KindleDeliveriesRepository(db)
user_preferences_repo = UserPreferencesRepository(db)
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
    download_history_repo=download_history_repo,
    last_books_repo=last_books_repo,
)
kindle_queue = KindleQueue(
    service=kindle_service,
    worker_concurrency=settings.kindle_worker_concurrency,
    user_concurrency=settings.kindle_user_concurrency,
    error_message_for_exception=user_message_for_exception,
    max_attempts=settings.kindle_max_job_attempts,
    retry_base_delay_seconds=settings.kindle_retry_base_delay_seconds,
)
ai_assistant = AiAssistant(settings.openai_api_key, settings.ai_model, settings.ai_enabled)


@router.message(Command("start"))
async def start(message: Message, command: CommandObject) -> None:
    log_user_action(message.from_user, message.chat.id, "start")
    if settings.access_control_enabled and message.from_user.id not in settings.admin_ids:
        existing = await access_repo.get_user(message.from_user.id)
        arg = (command.args or "").strip()
        if arg.startswith("invite_") and await access_repo.redeem_invite(arg.removeprefix("invite_"), message.from_user.id, message.from_user.username, message.from_user.full_name):
            await message.answer("Приглашение принято. Добро пожаловать в библиотеку.", reply_markup=main_reply_keyboard())
            return
        if existing and existing.status == "blocked":
            await message.answer("Доступ к библиотеке не открыт.")
            return
        if existing is None:
            await access_repo.request_access(message.from_user.id, message.from_user.username, message.from_user.full_name)
            await _notify_admins_about_request(message.bot, message.from_user)
            await message.answer("Доступ по приглашению.\n\nЯ отправил запрос администратору и напишу, когда вход откроют.")
            return
        if existing.status != "approved":
            await message.answer("Запрос уже отправлен.\n\nЯ сообщу, когда администратор откроет доступ.")
            return
    await telegram_retry(
        lambda: message.answer(
            "<b>Библиотека</b>\n\nЧто хочется почитать?\n\n"
            "Напиши название, автора или просто опиши книгу:\n"
            "«Дюна»\n"
            "«Пелевин»\n"
            "«что-то как 1984, но современнее»",
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


@router.message(Command("recommend"))
async def recommend_command(message: Message, command: CommandObject) -> None:
    query=(command.args or "").strip()
    if not query:
        await message.answer("Опиши книгу, автора или настроение — я сам разберу запрос.")
        return
    await send_ai_results(message, query)

@router.message(Command("invite"))
async def invite_command(message: Message, command: CommandObject) -> None:
    if message.from_user.id not in settings.admin_ids: return
    raw=(command.args or "1").strip()
    uses=int(raw) if raw.isdigit() and int(raw)>0 else 1
    code=await access_repo.create_invite(message.from_user.id,uses)
    me=await message.bot.get_me()
    await message.answer(f"Приглашение на {uses} вход(а):\n<code>https://t.me/{me.username}?start=invite_{code}</code>")

@router.callback_query(F.data.startswith("access_"))
async def access_decision(callback: CallbackQuery) -> None:
    if callback.from_user.id not in settings.admin_ids: return
    action,user_id_raw=callback.data.split(":",1); user_id=int(user_id_raw)
    approved=action=="access_approve"
    await access_repo.set_status(user_id,"approved" if approved else "blocked",callback.from_user.id)
    await callback.answer("Готово")
    await callback.message.edit_text((callback.message.text or "") + ("\n\n✅ Доступ открыт" if approved else "\n\n❌ Отклонено"))
    if approved:
        await callback.bot.send_message(user_id,"Администратор открыл доступ. Можно пользоваться ботом: /start")

@router.message(Command("favorites", "fav"))
async def favorites_command(message: Message) -> None:
    await _send_favorites_page(message, message.from_user.id, 0)

@router.callback_query(F.data.startswith("fav_page:"))
async def favorites_page(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    await _send_favorites_page(callback.message, callback.from_user.id, int(callback.data.split(":",1)[1]), edit=True)

@router.message(Command("history"))
async def history_command(message: Message) -> None:
    await message.answer(_history_text(await download_history_repo.recent(message.from_user.id)))

@router.message(Command("history_failed"))
async def history_failed_command(message: Message) -> None:
    await message.answer(_history_text(await download_history_repo.recent(message.from_user.id, status="failed"), failed=True))

@router.message(Command("last"))
async def last_command(message: Message) -> None:
    item = await last_books_repo.get(message.from_user.id)
    if item is None:
        await message.answer("<b>Последняя книга</b>\n\nПока пусто. Открой любую карточку — и она появится здесь.")
        return
    preferred = await _preferred_format(message.from_user.id) or "epub"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Открыть карточку", callback_data=f"book:{item.book_id}"))
    kb.row(InlineKeyboardButton(text=f"Скачать {preferred.upper()}", callback_data=f"dl:{item.book_id}:{preferred}"), InlineKeyboardButton(text="📤 Kindle", callback_data=f"kindle:{item.book_id}"))
    kb.row(InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav_add:{item.book_id}"))
    await message.answer(f"<b>Последняя книга</b>\n\n<b>{escape(item.title)}</b>" + (f"\n{escape(item.author)}" if item.author else ""), reply_markup=kb.as_markup())

@router.message(Command("admin_cache_stats"))
async def admin_cache_stats(message: Message) -> None:
    if message.from_user.id not in settings.admin_ids: return
    total, by_type, expired = await cache_repo.stats()
    lines = ["Cache stats", f"total: {total}", f"expired: {expired}"] + [f"{k}: {v}" for k,v in sorted(by_type.items())]
    await message.answer("\n".join(lines))

@router.message(Command("admin_cache_clear"))
async def admin_cache_clear(message: Message, command: CommandObject) -> None:
    if message.from_user.id not in settings.admin_ids: return
    deleted = await cache_repo.clear(all_rows=(command.args or "").strip().lower()=="all")
    await message.answer(f"Deleted {deleted} cache rows.")

@router.message(Command("admin_stats"))
async def admin_stats(message: Message) -> None:
    if message.from_user.id not in settings.admin_ids: return
    async with db.connect() as c:
        kindle_users = int((await (await c.execute("SELECT COUNT(*) FROM user_kindle_settings")).fetchone())[0])
        failed_kindle_today = int((await (await c.execute("SELECT COUNT(*) FROM kindle_deliveries WHERE status='failed' AND created_at>=?", (datetime.now().date().isoformat(),))).fetchone())[0])
    top = ", ".join(f"{fmt}:{count}" for fmt,count in await download_history_repo.top_formats()) or "—"
    await message.answer(
        "Stats\n"
        f"Kindle users: {kindle_users}\n"
        f"Favorites: {await favorites_repo.count()}\n"
        f"Telegram downloads today: {await download_history_repo.sent_today('telegram')}\n"
        f"Kindle sends today: {await download_history_repo.sent_today('kindle')}\n"
        f"Failed Kindle today: {failed_kindle_today}\n"
        f"Top formats: {top}\n"
        f"Active search sessions: {len(search_sessions)+len(author_sessions)}\n"
        f"Active queue jobs: {kindle_queue.active_jobs}"
    )


@router.message(F.text)
async def search_text(message: Message) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return

    if text == FAVORITES_BUTTON:
        await favorites_command(message); return
    if text == HISTORY_BUTTON:
        await history_command(message); return
    if text == LAST_BUTTON:
        await last_command(message); return
    if text == KINDLE_BUTTON:
        return
    await send_ai_results(message, text)


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
    preferred_format = await _preferred_format(callback.from_user.id)
    await last_books_repo.upsert(callback.from_user.id, details.book_id, details.title, ", ".join(details.authors) or None, "opened")
    is_favorite = await favorites_repo.exists(callback.from_user.id, details.book_id)
    await telegram_retry(
        lambda: callback.message.answer(
            _book_text(details),
            reply_markup=_formats_keyboard(details, preferred_format=preferred_format, is_favorite=is_favorite),
        )
    )

@router.callback_query(F.data.startswith("annotation:"))
async def show_full_annotation(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    details = await flibusta.details(callback.data.split(":",1)[1])
    await telegram_retry(lambda: callback.message.answer(_book_text(details, full_annotation=True)))

@router.callback_query(F.data.startswith("fav_add:"))
async def add_favorite(callback: CallbackQuery) -> None:
    await callback_answer(callback, "Добавлено в избранное")
    details = await flibusta.details(callback.data.split(":",1)[1])
    await favorites_repo.add(callback.from_user.id, details.book_id, details.title, ", ".join(details.authors) or None)

@router.callback_query(F.data.startswith("fav_remove:"))
async def remove_favorite(callback: CallbackQuery) -> None:
    await callback_answer(callback, "Убрано из избранного")
    await favorites_repo.remove(callback.from_user.id, callback.data.split(":",1)[1])


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
        await telegram_retry(lambda: callback.message.answer("Эта выдача уже устарела. Отправь запрос ещё раз."))
        return

    if callback.from_user.id != session.user_id or callback.message.chat.id != session.chat_id:
        await telegram_retry(lambda: callback.answer("Это не твоя выдача.", show_alert=True))
        return

    try:
        page = int(page_raw)
    except ValueError:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Не смог открыть эту страницу выдачи."))
        return

    page_count = total_pages(len(session.results))
    if page < 0 or page >= page_count:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Такой страницы уже нет."))
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
        await telegram_retry(lambda: callback.message.answer("Эта выдача авторов уже устарела. Отправь запрос ещё раз."))
        return

    if callback.from_user.id != session.user_id or callback.message.chat.id != session.chat_id:
        await telegram_retry(lambda: callback.answer("Это не твоя выдача.", show_alert=True))
        return

    try:
        page = int(page_raw)
    except ValueError:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Не смог открыть эту страницу авторов."))
        return

    page_count = total_pages(len(session.authors))
    if page < 0 or page >= page_count:
        await callback_answer(callback)
        await telegram_retry(lambda: callback.message.answer("Такой страницы уже нет."))
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
        await telegram_retry(lambda: callback.message.answer("Эта выдача авторов уже устарела. Отправь запрос ещё раз."))
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

@router.callback_query(F.data.startswith("retry_"))
async def retry_search(callback: CallbackQuery) -> None:
    action, sid = callback.data.split(":",1)
    query = retry_sessions.get(sid)
    await callback_answer(callback)
    if query is None:
        await callback.message.answer("Этот повтор уже устарел.")
        return
    if action == "retry_short":
        await send_smart_results(callback.message, " ".join(query.split()[:3]))
    elif action == "retry_book":
        await send_search_results(callback.message, query)
    elif action == "retry_author":
        await send_author_results(callback.message, query)
    else:
        await send_smart_results(callback.message, _clean_query(query))


@router.callback_query(F.data.startswith("dl:"))
async def download_book(callback: CallbackQuery) -> None:
    _, book_id, fmt = callback.data.split(":", 2)
    started_at = monotonic()
    log_user_action(callback.from_user, callback.message.chat.id, "download_start", book_id=book_id, fmt=fmt)
    await callback_answer(callback, "Скачиваю...")
    status_message = await telegram_retry(
            lambda: callback.message.answer(f"Скачиваю {escape(fmt.upper())}…")
    )

    if callback.from_user.id not in settings.admin_ids and await download_history_repo.count_recent_downloads(callback.from_user.id) >= settings.download_rate_limit_per_hour:
        await telegram_retry(lambda: callback.message.answer("Лимит скачиваний пока исчерпан. Попробуй позже."))
        return
    details = None
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
        await download_history_repo.add(user_id=callback.from_user.id,book_id=book_id,title=details.title if details else None,author=", ".join(details.authors) if details else None,format=fmt,filename=None,file_size_bytes=None,delivery_target="telegram",status="failed",error=str(exc))
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
        await download_history_repo.add(user_id=callback.from_user.id,book_id=book_id,title=details.title,author=", ".join(details.authors) or None,format=fmt,filename=filename,file_size_bytes=len(content),delivery_target="telegram",status="failed",error="telegram upload too large")
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
            lambda: status_message.edit_text(f"Файл скачан ({size_mb:.1f} МБ). Отправляю в Telegram…")
        )
    else:
        await telegram_retry(
            lambda: callback.message.answer(f"Файл скачан ({size_mb:.1f} МБ). Отправляю в Telegram…")
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
        await download_history_repo.add(user_id=callback.from_user.id,book_id=book_id,title=details.title,author=", ".join(details.authors) or None,format=fmt,filename=filename,file_size_bytes=len(content),delivery_target="telegram",status="failed",error="telegram upload failed")
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
    await _remember_preferred_format(callback.from_user.id, fmt)
    await download_history_repo.add(user_id=callback.from_user.id,book_id=book_id,title=details.title,author=", ".join(details.authors) or None,format=fmt,filename=filename,file_size_bytes=len(content),delivery_target="telegram",status="sent")
    await last_books_repo.upsert(callback.from_user.id, book_id, details.title, ", ".join(details.authors) or None, "downloaded")


async def send_search_results(message: Message, query: str) -> None:
    if not _allow_search(message.from_user.id):
        await telegram_retry(lambda: message.answer("Слишком много запросов подряд. Подожди немного и попробуй снова."))
        return
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
        await _send_no_results(message, query)
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
    if not _allow_search(message.from_user.id):
        await telegram_retry(lambda: message.answer("Слишком много запросов подряд. Подожди немного и попробуй снова."))
        return
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
        await _send_no_results(message, query)
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
    if not _allow_search(message.from_user.id):
        await telegram_retry(lambda: message.answer("Слишком много запросов подряд. Подожди немного и попробуй снова."))
        return
    started_at = monotonic()
    analysis = analyze_query(query)
    cleaned = _clean_query(analysis.cleaned or query)
    if analysis.format_hint:
        await _remember_preferred_format(message.from_user.id, analysis.format_hint)
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
    top_author_is_exact = bool(authors and _norm(authors[0].name) == _norm(cleaned))
    top_book_is_exact = bool(books and _norm(_base_title(books[0].title)) == _norm(query))

    if (analysis.likely_author or top_author_is_exact) and not analysis.quoted_title and not top_book_is_exact and authors:
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

    if books and authors and not top_book_is_exact and not top_author_is_exact and not analysis.quoted_title:
        book_session = _create_search_session(message.from_user.id, message.chat.id, used_query, books)
        author_session = _create_author_session(message.from_user.id, message.chat.id, used_query, authors)
        await telegram_retry(lambda: message.answer(_combined_results_text(used_query, books, authors), reply_markup=_combined_results_keyboard(book_session, author_session)))
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

    await _send_no_results(message, query)

async def send_ai_results(message: Message, query: str) -> None:
    intent = await ai_assistant.understand(query)
    await message.answer(intent.reply)
    for candidate in intent.search_queries:
        raw_books, raw_authors = await flibusta.search_all(candidate, book_limit=settings.search_results_limit, author_limit=settings.search_results_limit)
        books = _rank_and_dedupe_books(raw_books, candidate)
        authors = _rank_authors(raw_authors, candidate)
        if books or authors:
            if books and authors:
                bs=_create_search_session(message.from_user.id,message.chat.id,candidate,books); aus=_create_author_session(message.from_user.id,message.chat.id,candidate,authors)
                await message.answer(_combined_results_text(candidate,books,authors),reply_markup=_combined_results_keyboard(bs,aus))
            elif books:
                session=_create_search_session(message.from_user.id,message.chat.id,candidate,books,title=f"Подобрал по запросу: <b>{escape(candidate)}</b>")
                await message.answer(_search_results_text(session),reply_markup=_search_results_keyboard(session))
            else:
                session=_create_author_session(message.from_user.id,message.chat.id,candidate,authors)
                await message.answer(_author_results_text(session),reply_markup=_author_results_keyboard(session))
            return
    await _send_no_results(message, query)


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
    heading = title or session.title or f"<b>Книги</b>\nПо запросу: <b>{escape(session.query)}</b>"
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
        f"<b>Авторы</b>\nПо запросу: <b>{escape(session.query)}</b>\n"
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
    logger.info("startup database_path=%s search_limit=%s kindle_enabled=%s kindle_queue=%s/%s attachment_mb=%s",settings.database_path,settings.search_results_limit,'yes' if _smtp_config_present() else 'no',settings.kindle_worker_concurrency,settings.kindle_user_concurrency,settings.kindle_max_attachment_mb)


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


def _book_text(details: BookDetails, full_annotation: bool = False) -> str:
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
        annotation = details.annotation
        if not full_annotation and len(annotation) > settings.book_annotation_max_chars:
            annotation = annotation[: settings.book_annotation_max_chars - 1].rstrip() + "…"
        parts.append(escape(annotation))
    if not details.formats:
        parts.append("Доступные форматы не найдены.")
    elif not any(item.code in {"epub", "fb2", "txt", "mobi", "pdf"} for item in details.formats):
        parts.append("Kindle-совместимый формат не найден.")
    return "\n\n".join(parts)


def _formats_keyboard(details: BookDetails, preferred_format: str | None = None, is_favorite: bool = False):
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
    kindle_code=next((c for c in [preferred_format,'epub','fb2','txt','mobi','pdf'] if c and any(f.code==c for f in details.formats)),None)
    if kindle_code:
        keyboard.row(InlineKeyboardButton(text=f"📤 Отправить {kindle_code.upper()} на Kindle", callback_data=f"kindle:{details.book_id}"))
    if details.annotation and len(details.annotation) > settings.book_annotation_max_chars:
        keyboard.row(InlineKeyboardButton(text="Показать всю аннотацию", callback_data=f"annotation:{details.book_id}"))
    keyboard.row(InlineKeyboardButton(text="✅ В избранном" if is_favorite else "⭐ В избранное", callback_data=f"{'fav_remove' if is_favorite else 'fav_add'}:{details.book_id}"))

    return keyboard.as_markup()


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=FAVORITES_BUTTON),
                KeyboardButton(text=HISTORY_BUTTON),
            ],
            [
                KeyboardButton(text=LAST_BUTTON),
                KeyboardButton(text=KINDLE_BUTTON),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Книга, автор или что хочется почитать",
    )


async def _preferred_format(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    pref=await user_preferences_repo.get(user_id)
    return pref.preferred_download_format if pref else None


async def _remember_preferred_format(user_id: int | None, fmt: str) -> None:
    if user_id is None:
        return
    await user_preferences_repo.upsert(user_id, download_format=fmt)


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

def _allow_search(user_id: int) -> bool:
    if user_id in settings.admin_ids: return True
    cutoff = time() - 60
    items = [value for value in search_timestamps.get(user_id, []) if value >= cutoff]
    if len(items) >= settings.search_rate_limit_per_minute:
        search_timestamps[user_id] = items
        return False
    items.append(time()); search_timestamps[user_id] = items; return True

async def _send_no_results(message: Message, query: str) -> None:
    sid=uuid4().hex[:10]; retry_sessions[sid]=query; _prune_sessions(retry_sessions)
    kb=InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Попробовать короче",callback_data=f"retry_short:{sid}"))
    kb.row(InlineKeyboardButton(text="Искать как книгу",callback_data=f"retry_book:{sid}"),InlineKeyboardButton(text="Искать как автора",callback_data=f"retry_author:{sid}"))
    kb.row(InlineKeyboardButton(text="Убрать кавычки и повторить",callback_data=f"retry_clean:{sid}"))
    await telegram_retry(lambda: message.answer(f"Ничего не найдено по запросу: <b>{escape(query)}</b>",reply_markup=kb.as_markup()))

def _combined_results_text(query,books,authors):
    book_lines="\n".join(f"• {escape(b.title)}" + (f" — {escape(b.author)}" if b.author else "") for b in books[:5])
    author_lines="\n".join(f"• {escape(a.name)}" for a in authors[:5])
    return f"<b>Нашёл варианты</b>\nПо запросу: <b>{escape(query)}</b>\n\n<b>Книги</b>\n{book_lines}\n\n<b>Авторы</b>\n{author_lines}"

def _combined_results_keyboard(book_session,author_session):
    kb=InlineKeyboardBuilder()
    for item in book_session.results[:5]: kb.row(InlineKeyboardButton(text=(item.title if not item.author else f"{item.title} - {item.author}")[:64],callback_data=f"book:{item.book_id}"))
    for item in author_session.authors[:5]: kb.row(InlineKeyboardButton(text=f"Автор: {item.name}"[:64],callback_data=f"author:{author_session.session_id}:{item.author_id}"))
    kb.row(InlineKeyboardButton(text="Показать больше книг",callback_data=f"page:{book_session.session_id}:0"),InlineKeyboardButton(text="Показать больше авторов",callback_data=f"apage:{author_session.session_id}:0"))
    return kb.as_markup()

async def _send_favorites_page(message: Message, user_id: int, page: int, edit: bool=False):
    items=await favorites_repo.list(user_id,limit=8,offset=page*8); count=await favorites_repo.count(user_id)
    if not items:
        await (message.edit_text("<b>Избранное</b>\n\nПока пусто.") if edit else message.answer("<b>Избранное</b>\n\nПока пусто.")); return
    kb=InlineKeyboardBuilder()
    for item in items:
        kb.row(InlineKeyboardButton(text=(item.title if not item.author else f"{item.title} — {item.author}")[:64],callback_data=f"book:{item.book_id}"),InlineKeyboardButton(text="✕",callback_data=f"fav_remove:{item.book_id}"))
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text="<<",callback_data=f"fav_page:{page-1}"))
    if (page+1)*8<count: nav.append(InlineKeyboardButton(text=">>",callback_data=f"fav_page:{page+1}"))
    if nav: kb.row(*nav)
    text=f"<b>Избранное</b>\n\nКниг: {count}"
    await (message.edit_text(text,reply_markup=kb.as_markup()) if edit else message.answer(text,reply_markup=kb.as_markup()))

def _history_text(items:list[DownloadHistoryItem],failed:bool=False)->str:
    if not items: return "<b>Неудачные отправки</b>\n\nПока пусто." if failed else "<b>История</b>\n\nПока пусто."
    head="<b>Неудачные отправки</b>" if failed else "<b>История</b>"
    lines=[head]
    for item in items:
        lines.append(f"{item.created_at[:16]} — {item.title or item.book_id} [{item.format}] → {item.delivery_target}" + (f" ({item.error})" if failed and item.error else ""))
    return "\n".join(lines)

async def _notify_admins_about_request(bot: Bot, user: User) -> None:
    kb=InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Разрешить",callback_data=f"access_approve:{user.id}"),InlineKeyboardButton(text="❌ Отклонить",callback_data=f"access_reject:{user.id}"))
    label=escape(user.full_name)
    if user.username: label += f" @{escape(user.username)}"
    for admin_id in settings.admin_ids:
        await telegram_retry(lambda admin_id=admin_id: bot.send_message(admin_id,f"Запрос доступа:\n{label}\n<code>{user.id}</code>",reply_markup=kb.as_markup()))


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="search", description="поиск книг"),
            BotCommand(command="author", description="поиск авторов"),
            BotCommand(command="recommend", description="подобрать книгу"),
            BotCommand(command="kindle_email", description="сохранить Kindle e-mail"),
            BotCommand(command="kindle_help", description="настройка Send to Kindle"),
            BotCommand(command="kindle_setup", description="настройка Kindle"),
            BotCommand(command="kindle_status", description="статус Kindle"),
            BotCommand(command="kindle_remove", description="удалить Kindle e-mail"),
            BotCommand(command="kindle_history", description="история Kindle"),
            BotCommand(command="kindle_format", description="формат Kindle"),
            BotCommand(command="kindle_retry", description="повторить Kindle"),
            BotCommand(command="kindle", description="меню Kindle"),
            BotCommand(command="favorites", description="избранные книги"),
            BotCommand(command="history", description="история отправок"),
            BotCommand(command="history_failed", description="неудачные отправки"),
            BotCommand(command="last", description="последняя книга"),
            BotCommand(command="start", description="открыть меню"),
            BotCommand(command="admin", description="админка"),
        ]
    )


async def main() -> None:
    log_startup_config()
    await db.initialize()
    imported = await user_preferences_repo.import_json_once(Path("user_prefs.json"))
    if imported:
        logger.info("imported legacy user preferences count=%s", imported)
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
    if settings.access_control_enabled:
        dispatcher.message.middleware(AccessMiddleware(access_repo, settings.admin_ids))
        dispatcher.callback_query.middleware(AccessMiddleware(access_repo, settings.admin_ids))
    dispatcher.include_router(
        build_kindle_router(
            db=db,
            settings_repo=kindle_settings_repo,
            deliveries_repo=kindle_deliveries_repo,
            preferences_repo=user_preferences_repo,
            kindle_queue=kindle_queue,
            smtp_from_email=settings.smtp_from_email,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_config_present=_smtp_config_present(),
            default_format=settings.kindle_default_format,
            max_attachment_mb=settings.kindle_max_attachment_mb,
            admin_user_ids=settings.admin_ids,
            retention_days=settings.kindle_delivery_log_retention_days,
            export_include_full_emails=settings.admin_export_include_full_emails,
        )
    )
    dispatcher.include_router(
        build_admin_router(
            access_repo=access_repo,
            cache_repo=cache_repo,
            history_repo=download_history_repo,
            favorites_repo=favorites_repo,
            deliveries_repo=kindle_deliveries_repo,
            kindle_queue=kindle_queue,
            admin_ids=settings.admin_ids,
        )
    )
    dispatcher.include_router(router)
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
