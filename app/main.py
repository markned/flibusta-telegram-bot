from __future__ import annotations

import logging
import re
from asyncio import sleep
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
    BotCommandScopeChat,
    BotCommandScopeDefault,
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
from app.flibusta import AuthorResult, FlibustaClient, FlibustaError, SearchResult
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
from app.services.covers.download import download_cover
from app.services.covers.providers import DisabledCoverProvider, FlibustaCoverProvider, GoogleBooksCoverProvider, OpenLibraryCoverProvider
from app.services.covers.resolver import CoverResolver
from app.services.ebook_metadata import EbookMetadataPolisher
from app.services.kindle import KindleService
from app.services.kindle_queue import KindleQueue
from app.services.cached_flibusta import CachedFlibustaClient
from app.services.query_analyzer import analyze_query
from app.services.intent_router import IntentKind, route_intent
from app.services.pending_recommendations import PendingRecommendationStore
from app.services.recommendation_clarifier import build_recommendation_clarification
from app.services.ai_assistant import AiAssistant
from app.services.recommendation_packs import get_recommendation_pack
from app.services.recommendation_filters import is_bad_recommendation_candidate, is_weak_recommendation_anchor
from app.services.recommendations import merge_recommendation_queries
from app.services.discovery.idea_generator import BookIdeaGenerator
from app.services.discovery.flibusta_matcher import FlibustaMatcher
from app.services.discovery.recommender import DiscoveryRateLimiter, DiscoveryRecommender
from app.services.discovery.web_search import DisabledWebSearchProvider, TavilyWebSearchProvider
from app.middlewares.access import AccessMiddleware
from app.state import (
    AuthorSession,
    SearchSession,
    author_sessions,
    create_author_session as _create_author_session,
    create_search_session as _create_search_session,
    prune_sessions as _prune_sessions,
    retry_sessions,
    search_sessions,
    search_timestamps,
)
from app.services.search_logic import (
    base_title as _base_title,
    clean_query as _clean_query,
    fallback_queries as _fallback_queries,
    norm as _norm,
    rank_and_dedupe_books as _rank_and_dedupe_books,
    rank_authors as _rank_authors,
)
from app.ui.library import (
    author_results_keyboard as _author_results_keyboard,
    author_results_text as _author_results_text,
    book_text as render_book_text,
    combined_results_keyboard as _combined_results_keyboard,
    combined_results_text as _combined_results_text,
    formats_keyboard as render_formats_keyboard,
    history_text as _history_text,
    main_reply_keyboard,
    recommendation_text as _recommendation_text,
    recommendation_details_text as _recommendation_details_text,
    search_results_keyboard as _search_results_keyboard,
    search_results_text as _search_results_text,
)
from app.ui.home import (
    back_home_keyboard,
    help_keyboard,
    help_text,
    home_keyboard,
    home_text,
    search_help_text,
)

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
HELP_BUTTON = "❓ Помощь"

router = Router()

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
cover_providers = []
for provider_name in settings.cover_provider_order_list:
    if provider_name == "flibusta":
        cover_providers.append(FlibustaCoverProvider())
    elif provider_name == "openlibrary":
        cover_providers.append(OpenLibraryCoverProvider(timeout_seconds=settings.cover_lookup_timeout_seconds))
    elif provider_name == "google_books":
        cover_providers.append(GoogleBooksCoverProvider(api_key=settings.google_books_api_key, timeout_seconds=settings.cover_lookup_timeout_seconds))
    elif provider_name == "disabled":
        cover_providers.append(DisabledCoverProvider())
cover_resolver = CoverResolver(
    cache_repo=cache_repo,
    providers=cover_providers,
    enabled=settings.cover_lookup_enabled,
    cache_ttl_seconds=settings.cover_cache_ttl_seconds,
    negative_cache_ttl_seconds=settings.cover_negative_cache_ttl_seconds,
    min_confidence=settings.cover_min_confidence,
    min_width=settings.cover_min_width,
    min_height=settings.cover_min_height,
)
ebook_metadata_polisher = EbookMetadataPolisher(
    tool=settings.kindle_metadata_tool,
    timeout_seconds=settings.kindle_metadata_timeout_seconds,
    require_tool=settings.kindle_metadata_require_calibre,
)
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
    host=settings.smtp_effective_host,
    port=settings.smtp_effective_port,
    username=settings.smtp_username,
    password=settings.smtp_password,
    from_email=settings.smtp_from_email,
    starttls=settings.smtp_effective_starttls,
    provider=settings.smtp_provider_normalized,
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
    cover_resolver=cover_resolver,
    cover_download_max_bytes=settings.cover_max_download_mb * 1024 * 1024,
    cover_download_timeout_seconds=settings.cover_lookup_timeout_seconds,
    metadata_polisher=ebook_metadata_polisher,
    metadata_polish_enabled=settings.kindle_metadata_polish_enabled,
    embed_cover_enabled=settings.kindle_embed_cover_enabled,
    filename_template=settings.kindle_filename_template,
    strict_metadata_title_author=settings.kindle_strict_metadata_title_author,
)
kindle_queue = KindleQueue(
    service=kindle_service,
    worker_concurrency=settings.kindle_worker_concurrency,
    user_concurrency=settings.kindle_user_concurrency,
    error_message_for_exception=user_message_for_exception,
    max_attempts=settings.kindle_max_job_attempts,
    retry_base_delay_seconds=settings.kindle_retry_base_delay_seconds,
)
ai_assistant = AiAssistant(settings.openai_api_key, settings.ai_model, settings.ai_enabled,cache_repo=cache_repo,cache_ttl_seconds=settings.ai_intent_cache_ttl_seconds)

_web_provider = TavilyWebSearchProvider(settings.discovery_web_api_key, timeout_seconds=settings.discovery_timeout_seconds, max_snippet_chars=settings.discovery_max_web_snippet_chars) if settings.discovery_web_active else DisabledWebSearchProvider()
pending_recommendations = PendingRecommendationStore(settings.recommendation_confirmation_ttl_seconds)
discovery_recommender = DiscoveryRecommender(
    flibusta=flibusta,
    cache_repo=cache_repo,
    idea_generator=BookIdeaGenerator(settings.openai_api_key, settings.discovery_model or settings.ai_model, settings.ai_enabled, cache_repo=cache_repo, cache_ttl_seconds=settings.discovery_cache_ttl_seconds, max_ideas=settings.discovery_max_book_ideas, timeout_seconds=settings.discovery_timeout_seconds),
    matcher=FlibustaMatcher(flibusta, max_checks=settings.discovery_max_flibusta_checks, max_final_results=settings.discovery_max_final_results),
    web_provider=_web_provider,
    favorites_repo=favorites_repo,
    history_repo=download_history_repo,
    preferences_repo=user_preferences_repo,
    cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
    max_web_results=settings.discovery_max_web_results,
    web_enabled=settings.discovery_web_active,
    rate_limiter=DiscoveryRateLimiter(settings.discovery_user_daily_limit, settings.discovery_global_daily_limit),
    concurrency=settings.discovery_concurrency,
)


def _reply_keyboard():
    return main_reply_keyboard() if settings.ui_reply_keyboard_enabled else None


def _home_inline_keyboard():
    return home_keyboard() if settings.ui_home_inline_buttons else None


async def send_home(message: Message) -> None:
    if not settings.ui_home_inline_buttons:
        await telegram_retry(lambda: message.answer(home_text(), reply_markup=_reply_keyboard()))
        return
    await telegram_retry(
        lambda: message.answer(
            home_text(),
            reply_markup=_home_inline_keyboard(),
        )
    )
    if settings.ui_reply_keyboard_enabled:
        await telegram_retry(lambda: message.answer("Главное меню — внизу.", reply_markup=main_reply_keyboard()), attempts=1)


async def send_help(message: Message) -> None:
    await telegram_retry(
        lambda: message.answer(
            help_text(),
            reply_markup=help_keyboard(),
        )
    )


@router.message(Command("start"))
async def start(message: Message, command: CommandObject) -> None:
    log_user_action(message.from_user, message.chat.id, "start")
    if settings.access_control_enabled and message.from_user.id not in settings.admin_ids:
        existing = await access_repo.get_user(message.from_user.id)
        arg = (command.args or "").strip()
        if arg.startswith("invite_") and await access_repo.redeem_invite(arg.removeprefix("invite_"), message.from_user.id, message.from_user.username, message.from_user.full_name):
            await message.answer("Приглашение принято. Добро пожаловать в библиотеку.", reply_markup=_reply_keyboard())
            await send_home(message)
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
    await send_home(message)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await send_help(message)


@router.message(Command("search"))
async def search_command(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        log_user_action(message.from_user, message.chat.id, "search_empty")
        await telegram_retry(
            lambda: message.answer("Напиши название книги обычным сообщением — я поищу.")
        )
        return
    await send_search_results(message, query)


@router.message(Command("author"))
async def author_command(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        log_user_action(message.from_user, message.chat.id, "author_search_empty")
        await telegram_retry(
            lambda: message.answer("Напиши имя автора обычным сообщением — я найду его книги.")
        )
        return
    await send_author_results(message, query)


@router.message(Command("recommend"))
async def recommend_command(message: Message, command: CommandObject) -> None:
    if not _assistant_ui_enabled():
        await message.answer("Подборки сейчас отключены. Напиши название книги или автора — я поищу в каталоге.")
        return
    query=(command.args or "").strip()
    if not query:
        await message.answer("Опиши книгу, автора или настроение обычным сообщением — я сам разберу запрос.")
        return
    if await send_discovery_results(message, query, mode="recommend", use_web=False):
        return
    await send_ai_results(message, query)

@router.message(Command("discover"))
async def discover_command(message: Message, command: CommandObject) -> None:
    if not settings.discovery_enabled:
        await message.answer("Веб-подборки сейчас отключены. Напиши название книги или автора — я поищу в каталоге.")
        return
    query=(command.args or "").strip()
    if not query:
        await message.answer("Напиши тему подборки обычным сообщением.")
        return
    use_web=settings.discovery_enabled and settings.discovery_use_web
    if await send_discovery_results(message, query, mode="discover", use_web=use_web):
        return
    await send_smart_results(message, query)

@router.message(Command("discover_web"))
async def discover_web_command(message: Message, command: CommandObject) -> None:
    if not settings.discovery_enabled:
        await message.answer("Веб-подборки сейчас отключены. Напиши название книги или автора — я поищу в каталоге.")
        return
    query=(command.args or "").strip()
    if not query:
        await message.answer("Напиши тему подборки обычным сообщением.")
        return
    configured=settings.discovery_web_active
    if not configured:
        await message.answer("Веб-подборки сейчас не настроены. Попробую без интернета.")
    if await send_discovery_results(message, query, mode="discover_web", use_web=configured):
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
        await callback.bot.send_message(user_id,"Администратор открыл доступ. Можно пользоваться ботом: напиши название книги или нажми кнопку меню.",reply_markup=_reply_keyboard())

@router.message(Command("favorites", "fav"))
async def favorites_command(message: Message) -> None:
    await _send_favorites_page(message, message.from_user.id, 0)


@router.callback_query(F.data == "home_favorites")
async def home_favorites(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    await _send_favorites_page(callback.message, callback.from_user.id, 0)

@router.callback_query(F.data.startswith("fav_page:"))
async def favorites_page(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    await _send_favorites_page(callback.message, callback.from_user.id, int(callback.data.split(":",1)[1]), edit=True)

@router.message(Command("history"))
async def history_command(message: Message) -> None:
    await _send_history_message(message, message.from_user.id)

@router.message(Command("history_failed"))
async def history_failed_command(message: Message) -> None:
    await _send_history_message(message, message.from_user.id, failed=True)


@router.callback_query(F.data == "home_history")
async def home_history(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    await _send_history_message(callback.message, callback.from_user.id)

@router.message(Command("last"))
async def last_command(message: Message) -> None:
    await _send_last_message(message, message.from_user.id)


@router.callback_query(F.data == "home_last")
async def home_last(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    await _send_last_message(callback.message, callback.from_user.id)


@router.callback_query(F.data == "home")
async def home_callback(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    try:
        await callback.message.edit_text(home_text(), reply_markup=_home_inline_keyboard())
    except TelegramBadRequest:
        await send_home(callback.message)


@router.callback_query(F.data == "home_help")
async def help_callback(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    try:
        await callback.message.edit_text(help_text(), reply_markup=help_keyboard())
    except TelegramBadRequest:
        await send_help(callback.message)


@router.callback_query(F.data == "home_search_help")
async def search_help_callback(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    try:
        await callback.message.edit_text(search_help_text(), reply_markup=back_home_keyboard())
    except TelegramBadRequest:
        await callback.message.answer(search_help_text(), reply_markup=back_home_keyboard())


async def _send_history_message(message: Message, user_id: int, *, failed: bool = False) -> None:
    items = await download_history_repo.recent(user_id, status="failed" if failed else "sent")
    await message.answer(_history_text(items, failed=failed), reply_markup=back_home_keyboard())


async def _send_last_message(message: Message, user_id: int) -> None:
    item = await last_books_repo.get(user_id)
    if item is None:
        await message.answer("<b>Последняя книга</b>\n\nПока пусто. Открой любую карточку — и она появится здесь.", reply_markup=back_home_keyboard())
        return
    preferred = await _preferred_format(user_id) or "epub"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Открыть карточку", callback_data=f"book:{item.book_id}"))
    kb.row(InlineKeyboardButton(text=f"⬇️ {preferred.upper()}", callback_data=f"dl:{item.book_id}:{preferred}"), InlineKeyboardButton(text="📤 Kindle", callback_data=f"kindle:{item.book_id}"))
    kb.row(InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav_add:{item.book_id}"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
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

@router.message(Command("admin_discovery_status"))
async def admin_discovery_status(message: Message) -> None:
    if message.from_user.id not in settings.admin_ids: return
    total, by_type, _ = await cache_repo.stats()
    discovery_rows = sum(count for kind, count in by_type.items() if kind.startswith("discovery_"))
    await message.answer(
        "<b>Discovery</b>\n"
        f"enabled: {'yes' if settings.discovery_enabled else 'no'}\n"
        f"web enabled: {'yes' if settings.discovery_use_web else 'no'}\n"
        f"provider: {escape(settings.discovery_web_provider)}\n"
        f"api key present: {'yes' if bool(settings.discovery_web_api_key) else 'no'}\n"
        f"max web results: {settings.discovery_max_web_results}\n"
        f"max snippet chars: {settings.discovery_max_web_snippet_chars}\n"
        f"max Flibusta checks: {settings.discovery_max_flibusta_checks}\n"
        f"daily limits: {settings.discovery_user_daily_limit}/{settings.discovery_global_daily_limit}\n"
        f"cache TTL: {settings.discovery_cache_ttl_seconds}\n"
        f"Tavily active for discovery: {'yes' if _tavily_configured() else 'no'}\n"
        f"discovery cache rows: {discovery_rows}"
    )

@router.message(Command("admin_intent"))
async def admin_intent(message: Message, command: CommandObject) -> None:
    if message.from_user.id not in settings.admin_ids: return
    query=(command.args or "").strip()
    if not query:
        await message.answer("Использование: /admin_intent <запрос>")
        return
    decision=route_intent(query)
    ai_called=decision.kind in {IntentKind.RECOMMENDATION, IntentKind.DISCOVERY_OPTIONAL}
    discovery_called=decision.kind == IntentKind.DISCOVERY_OPTIONAL
    ask_confirmation=ai_called and settings.recommendation_confirmation_required
    handler=_intent_handler(decision.kind)
    await message.answer(
        "<b>Intent dry-run</b>\n"
        f"kind: {decision.kind.value}\n"
        f"confidence: {decision.confidence:.2f}\n"
        f"original: {escape(_truncate(query))}\n"
        f"cleaned: {escape(decision.cleaned_query)}\n"
        f"search_query: {escape(decision.search_query or '—')}\n"
        f"author_part: {escape(decision.author_part or '—')}\n"
        f"title_part: {escape(decision.title_part or '—')}\n"
        f"topic: {escape(decision.topic or '—')}\n"
        f"reference_authors: {escape(', '.join(decision.reference_authors) or '—')}\n"
        f"format_hint: {escape(decision.format_hint or '—')}\n"
        f"reasons: {escape(', '.join(decision.reasons) or '—')}\n"
        f"AI would be called: {'yes' if ai_called else 'no'}\n"
        f"discovery would be called: {'yes' if discovery_called else 'no'}\n"
        f"Tavily would be called: {'yes' if discovery_called and _tavily_configured() else 'no'}\n"
        f"would ask confirmation: {'yes' if ask_confirmation else 'no'}\n"
        f"confirmation preview: {escape(build_recommendation_clarification(query, decision)) if ask_confirmation else '—'}\n"
        f"use_web planned: {'yes' if discovery_called and settings.discovery_use_web else 'no'}\n"
        f"handler: {handler}"
    )

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
        await message.answer("Открываю Kindle-меню.", reply_markup=back_home_keyboard())
        return
    if text == HELP_BUTTON:
        await send_help(message)
        return
    decision = route_intent(text)
    logger.info("user_id=%s intent=%s confidence=%.2f reasons=%s query_len=%s", message.from_user.id, decision.kind.value, decision.confidence, len(decision.reasons), len(text))
    logger.debug("intent_detail query=%s cleaned=%s topic=%s", _truncate(text), decision.cleaned_query, decision.topic)
    if decision.kind == IntentKind.AUTHOR_TITLE_SEARCH:
        if await send_author_title_results(message, decision.author_part or "", decision.title_part or ""):
            return
        await send_smart_results(message, text)
        return
    if decision.kind == IntentKind.AUTHOR_SEARCH:
        await send_author_results(message, decision.search_query or text)
        return
    if decision.kind in {IntentKind.DISCOVERY_OPTIONAL, IntentKind.RECOMMENDATION}:
        if not settings.ai_enabled and not settings.discovery_enabled:
            await send_smart_results(message, text)
            return
        if settings.recommendation_confirmation_required:
            await ask_recommendation_confirmation(message, text, decision)
            return
        if decision.kind == IntentKind.DISCOVERY_OPTIONAL and await send_discovery_results(message, decision.topic or text, mode="auto", use_web=settings.discovery_use_web):
            return
        await send_ai_results(message, text, topic=decision.topic, intent=decision.kind.value)
        return
    await send_smart_results(message, text)

async def ask_recommendation_confirmation(message: Message, query: str, decision) -> None:
    pending = pending_recommendations.create(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        original_query=query,
        intent_kind=decision.kind.value,
        topic=decision.topic or query,
        use_web=decision.kind == IntentKind.DISCOVERY_OPTIONAL and settings.discovery_use_web,
        mode="auto",
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Да, собрать подборку", callback_data=f"rec_confirm:{pending.pending_id}"))
    kb.row(InlineKeyboardButton(text="Нет, искать точную фразу", callback_data=f"rec_exact:{pending.pending_id}"))
    kb.row(InlineKeyboardButton(text="Отмена", callback_data=f"rec_cancel:{pending.pending_id}"))
    await message.answer(build_recommendation_clarification(query, decision), reply_markup=kb.as_markup())

def _pending_for_callback(callback: CallbackQuery):
    pending_id = callback.data.split(":", 1)[1]
    pending = pending_recommendations.get(pending_id)
    if pending is None:
        return None
    if pending.user_id != callback.from_user.id or pending.chat_id != callback.message.chat.id:
        return False
    return pending

class _CallbackMessageProxy:
    def __init__(self, callback: CallbackQuery):
        self.from_user = callback.from_user
        self.chat = callback.message.chat
        self.bot = callback.message.bot
        self._message = callback.message
    async def answer(self, *args, **kwargs):
        return await self._message.answer(*args, **kwargs)

@router.callback_query(F.data.startswith("rec_confirm:"))
async def confirm_recommendation(callback: CallbackQuery) -> None:
    pending = _pending_for_callback(callback)
    if pending is None:
        await callback.answer("Запрос устарел. Напиши его ещё раз.")
        return
    if pending is False:
        await callback.answer("Это не твой запрос.")
        return
    pending_recommendations.delete(pending.pending_id)
    await callback.answer()
    message = _CallbackMessageProxy(callback)
    if await send_discovery_results(message, pending.topic, mode=pending.mode, use_web=pending.use_web):
        return
    await send_ai_results(message, pending.original_query, topic=pending.topic, intent=pending.intent_kind)

@router.callback_query(F.data.startswith("rec_exact:"))
async def exact_recommendation(callback: CallbackQuery) -> None:
    pending = _pending_for_callback(callback)
    if pending is None:
        await callback.answer("Запрос устарел. Напиши его ещё раз.")
        return
    if pending is False:
        await callback.answer("Это не твой запрос.")
        return
    pending_recommendations.delete(pending.pending_id)
    await callback.answer()
    await send_smart_results(_CallbackMessageProxy(callback), pending.original_query)

@router.callback_query(F.data.startswith("rec_cancel:"))
async def cancel_recommendation(callback: CallbackQuery) -> None:
    pending = _pending_for_callback(callback)
    if pending is None:
        await callback.answer("Запрос устарел. Напиши его ещё раз.")
        return
    if pending is False:
        await callback.answer("Это не твой запрос.")
        return
    pending_recommendations.delete(pending.pending_id)
    await callback.answer("Ок, отменил.")
    try:
        await callback.message.edit_text("Ок, отменил.")
    except Exception:
        pass


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
    await send_book_card(callback.message, details, preferred_format=preferred_format, is_favorite=is_favorite)


async def send_book_card(message: Message, details, *, preferred_format: str | None, is_favorite: bool) -> None:
    text = render_book_text(details, settings.book_annotation_max_chars)
    keyboard = render_formats_keyboard(
        details,
        preferred_format=preferred_format,
        is_favorite=is_favorite,
        annotation_max_chars=settings.book_annotation_max_chars,
    )
    if not (settings.book_cover_ui_enabled and settings.book_cover_send_as_photo):
        await telegram_retry(lambda: message.answer(text, reply_markup=keyboard))
        return
    try:
        cover = await cover_resolver.resolve(
            title=details.title,
            authors=details.authors,
            flibusta_cover_url=details.cover_url,
        )
        if cover is None:
            await telegram_retry(lambda: message.answer(text, reply_markup=keyboard))
            return
        cover_image = await download_cover(
            cover.url,
            max_bytes=settings.cover_max_download_mb * 1024 * 1024,
            timeout=settings.cover_lookup_timeout_seconds,
        )
        caption, truncated = _photo_caption(text, settings.cover_card_caption_max_chars)
        await telegram_retry(
            lambda: message.answer_photo(
                BufferedInputFile(cover_image.content, cover_image.filename),
                caption=caption,
                reply_markup=keyboard,
            )
        )
        if truncated:
            await telegram_retry(lambda: message.answer(text))
    except Exception:
        logger.warning("book cover card failed book_id=%s", getattr(details, "book_id", "unknown"), exc_info=True)
        if settings.book_cover_fallback_to_text:
            await telegram_retry(lambda: message.answer(text, reply_markup=keyboard))
        else:
            raise


def _photo_caption(text: str, limit: int) -> tuple[str, bool]:
    limit = min(max(100, limit), 1024)
    if len(text) <= limit:
        return text, False
    return text[: limit - 1].rstrip() + "…", True

@router.callback_query(F.data.startswith("annotation:"))
async def show_full_annotation(callback: CallbackQuery) -> None:
    await callback_answer(callback)
    details = await flibusta.details(callback.data.split(":",1)[1])
    await telegram_retry(lambda: callback.message.answer(render_book_text(details, settings.book_annotation_max_chars, full_annotation=True)))

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
    elif action == "retry_wide":
        await send_ai_results(callback.message, query)
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

async def send_author_title_results(message: Message, author: str, title: str) -> bool:
    """Fast path for obvious 'author + title' queries like 'Лев Толстой исповедь'."""
    try:
        results = _rank_and_dedupe_books(
            await flibusta.search(_clean_query(title), limit=settings.search_results_limit),
            title,
        )
    except FlibustaError:
        return False
    matched = _filter_author_title_results(results, author)
    if not matched:
        matched = await _search_author_books_for_title(author, title)
    if not matched:
        return False
    session = _create_search_session(
        message.from_user.id,
        message.chat.id,
        f"{author} {title}",
        matched,
        title=f"<b>Книги</b>\nПо запросу: <b>{escape(author)} {escape(title)}</b>",
    )
    await message.answer(_search_results_text(session), reply_markup=_search_results_keyboard(session))
    return True

async def send_reversed_author_title_results(message: Message, query: str) -> bool:
    decision = route_intent(query)
    if decision.kind != IntentKind.AUTHOR_TITLE_SEARCH:
        return False
    return await send_author_title_results(message, decision.author_part or "", decision.title_part or "")


def _filter_author_title_results(results: list[SearchResult], author: str) -> list[SearchResult]:
    return [item for item in results if item.author and _author_name_matches(author, item.author)]


def _author_name_matches(expected: str, actual: str) -> bool:
    expected_norm = _norm(expected)
    actual_norm = _norm(actual)
    if not expected_norm or not actual_norm:
        return False
    if expected_norm in actual_norm or actual_norm in expected_norm:
        return True
    parts = [part for part in expected_norm.split() if part]
    surname = parts[-1] if parts else ""
    return bool(surname and surname in actual_norm)


def _title_matches(expected: str, actual: str) -> bool:
    expected_norm = _norm(_base_title(expected))
    actual_norm = _norm(_base_title(actual))
    if not expected_norm or not actual_norm:
        return False
    return actual_norm == expected_norm or expected_norm in actual_norm or actual_norm in expected_norm


async def _search_author_books_for_title(author: str, title: str) -> list[SearchResult]:
    try:
        authors = _rank_authors(
            await flibusta.search_authors(_clean_query(author), limit=min(settings.search_results_limit, 10)),
            author,
        )
    except (AttributeError, FlibustaError):
        return []

    for candidate in authors[:3]:
        if not _author_name_matches(author, candidate.name):
            continue
        try:
            author_name, books = await flibusta.author_books(candidate.author_id, limit=settings.search_results_limit)
        except (AttributeError, FlibustaError):
            continue
        matched = [SearchResult(item.book_id, item.title, item.author or author_name or candidate.name) for item in books if _title_matches(title, item.title)]
        if matched:
            return _rank_and_dedupe_books(matched, title)
    return []


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


async def send_smart_results(message: Message, query: str, *, show_no_results: bool = True) -> bool:
    if not _allow_search(message.from_user.id):
        await telegram_retry(lambda: message.answer("Слишком много запросов подряд. Подожди немного и попробуй снова."))
        return False
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
        return False

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
        return True

    if books and authors and not top_book_is_exact and not top_author_is_exact and not analysis.quoted_title:
        book_session = _create_search_session(message.from_user.id, message.chat.id, used_query, books)
        author_session = _create_author_session(message.from_user.id, message.chat.id, used_query, authors)
        await telegram_retry(lambda: message.answer(_combined_results_text(used_query, books, authors), reply_markup=_combined_results_keyboard(book_session, author_session)))
        return True

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
        return True

    if authors:
        session = _create_author_session(message.from_user.id, message.chat.id, used_query, authors)
        await telegram_retry(
            lambda: message.answer(
                f"Книг не нашёл, но нашёл авторов по запросу: <b>{escape(used_query)}</b>",
                reply_markup=_author_results_keyboard(session),
            )
        )
        return True

    if show_no_results:
        await _send_no_results(message, query)
    return False

async def send_discovery_results(message: Message, query: str, *, mode: str, use_web: bool) -> bool:
    if not settings.discovery_enabled:
        return False
    progress = await message.answer("Собираю идеи и проверяю каталог…")
    result = await discovery_recommender.recommend(message.from_user.id, query, mode, use_web)
    if result.note == "web_rate_limited":
        await _edit_progress(progress, "Лимит веб-подборок на сегодня исчерпан. Попробую обычный подбор без интернета.")
    if not result.books:
        await _edit_progress(progress, "Я нашёл идеи по теме, но не смог сопоставить их с книгами в каталоге. К сожалению, ничего подходящего не нашлось.")
        return False
    source = "интернет + библиотека" if result.used_web else "модель + библиотека"
    lines = [
        "<b>Нашёл один подходящий вариант</b>" if len(result.books) == 1 else "<b>Подборка</b>",
        f"Запрос: <b>{escape(query)}</b>",
        f"Источник: {source}",
        "",
        "Я проверил идеи из интернета и показываю только то, что нашлось в каталоге." if result.used_web else "Я показываю только книги, которые удалось найти в каталоге.",
        "",
    ]
    for index, book in enumerate(result.books, start=1):
        lines.append(f"{index}. <b>{escape(book.title)}</b> — {escape(book.author or 'автор не указан')}")
        if book.reason:
            lines.append(f"Почему: {escape(book.reason[:180])}")
    books = [SearchResult(item.book_id, item.title, item.author) for item in result.books]
    session = _create_search_session(
        message.from_user.id,
        message.chat.id,
        query,
        books,
        title=f"<b>Подборка</b>\nПо запросу: <b>{escape(query)}</b>",
    )
    await _edit_progress(progress, "Подборка готова.")
    await message.answer("\n".join(lines), reply_markup=_search_results_keyboard(session))
    return True

async def send_ai_results(message: Message, query: str, *, topic: str | None = None, intent: str | None = None) -> None:
    progress = await message.answer("Разбираю запрос…")
    analysis = analyze_query(query)
    try:
        intent = await ai_assistant.understand(query, topic=topic, intent=intent)
    except Exception:
        logger.exception("AI search preparation failed")
        intent = None
    if intent is None:
        await _edit_progress(progress, "Не смог разобрать запрос через AI. Ищу обычным способом.")
        await send_smart_results(message, query)
        return
    if analysis.recommendation_like and (intent.kind != "recommend" or _norm(query) in {_norm(item) for item in intent.search_queries}):
        await _edit_progress(progress, "Похоже, это просьба о подборке. Уточняю варианты…")
        intent = await ai_assistant.understand(query, force_recommend=True, topic=topic, intent=intent)
        if intent.kind != "recommend" or _norm(query) in {_norm(item) for item in intent.search_queries}:
            fallback = get_recommendation_pack(query) or _recommendation_fallback_queries(query)
            if fallback:
                intent = type(intent)("recommend", fallback, "Подбираю книги по теме.", [], "")
            else:
                if await send_smart_results(message, query, show_no_results=False):
                    return
                await _edit_progress(progress, "Не смог собрать надёжную подборку. Попробуй описать запрос чуть конкретнее.")
                return
    await _edit_progress(progress, intent.reply)
    candidate_queries = intent.search_queries
    if intent.kind == "recommend":
        candidate_queries = merge_recommendation_queries([item for item in intent.search_queries if not is_weak_recommendation_anchor(item, query)],get_recommendation_pack(topic or query),settings.ai_recommendation_max_queries_used)
        if not candidate_queries and topic:
            candidate_queries = [topic]
    grouped_books = []
    all_authors = []
    for index, candidate in enumerate(candidate_queries, start=1):
        await _edit_progress(progress, f"{intent.reply}\n\nИщу: <b>{escape(candidate)}</b> ({index}/{len(candidate_queries)})")
        raw_books, raw_authors = await flibusta.search_all(candidate, book_limit=settings.search_results_limit, author_limit=settings.search_results_limit)
        ranked_books = [b for b in _rank_and_dedupe_books(raw_books, candidate) if not (intent.kind=="recommend" and is_bad_recommendation_candidate(b.title,query,intent.negative_keywords))]
        if ranked_books:
            grouped_books.append(ranked_books[:settings.ai_recommendation_books_per_query] if intent.kind == "recommend" else ranked_books)
        all_authors.extend(_rank_authors(raw_authors, candidate))
        if intent.kind == "recommend":
            for author in _rank_authors(raw_authors, candidate)[:1]:
                try:
                    _, author_books = await flibusta.author_books(author.author_id, limit=4)
                    if author_books:
                        filtered=[b for b in author_books if not is_bad_recommendation_candidate(b.title,query,intent.negative_keywords)]
                        if filtered: grouped_books.append(filtered[:settings.ai_recommendation_books_per_query])
                except FlibustaError:
                    logger.info("Could not expand recommendation author_id=%s", author.author_id)
        if intent.kind=="recommend" and len(_interleave_book_groups(grouped_books))>=settings.ai_recommendation_target_results: break
    books = _interleave_book_groups(grouped_books) if intent.kind == "recommend" else _dedupe_books_preserving_order([item for group in grouped_books for item in group])
    authors = _dedupe_authors_preserving_order(all_authors)
    if intent.kind=="recommend" and len(books)<settings.ai_recommendation_min_results:
        await _edit_progress(progress,"Я не нашёл достаточно точных совпадений.")
        if await send_smart_results(message, query, show_no_results=False):
            return
        await _send_weak_recommendation(message,query)
        return
    if books or authors:
        label = ", ".join(candidate_queries)
        if intent.kind == "recommend" and books:
            selected=books[:settings.ai_recommendation_target_results]
            await _edit_progress(progress, "Читаю аннотации для подборки…")
            detailed=[]
            for book in selected[:settings.ai_recommendation_max_details]:
                try:
                    detailed.append((book, await flibusta.details(book.book_id)))
                except FlibustaError:
                    continue
            if detailed:
                selected=[book for book,_ in detailed]
                session=_create_search_session(message.from_user.id,message.chat.id,label,selected,title=_recommendation_text(query,len(selected)))
                await _edit_progress(progress, "Собрал подборку.")
                await message.answer(_recommendation_details_text(query,detailed),reply_markup=_search_results_keyboard(session))
                return
            session=_create_search_session(message.from_user.id,message.chat.id,label,selected,title=_recommendation_text(query,len(selected)))
            await _edit_progress(progress, "Собрал подборку.")
            await message.answer(_search_results_text(session),reply_markup=_search_results_keyboard(session))
            return
        if books and authors:
            bs=_create_search_session(message.from_user.id,message.chat.id,label,books); aus=_create_author_session(message.from_user.id,message.chat.id,label,authors)
            await _edit_progress(progress, "Нашёл несколько направлений.")
            await message.answer(_combined_results_text(label,books,authors),reply_markup=_combined_results_keyboard(bs,aus))
        elif books:
            session=_create_search_session(message.from_user.id,message.chat.id,label,books,title=f"<b>Подобрал варианты</b>\nПо запросам: <b>{escape(label)}</b>")
            await _edit_progress(progress, "Нашёл книги.")
            await message.answer(_search_results_text(session),reply_markup=_search_results_keyboard(session))
        else:
            session=_create_author_session(message.from_user.id,message.chat.id,label,authors)
            await _edit_progress(progress, "Нашёл авторов.")
            await message.answer(_author_results_text(session),reply_markup=_author_results_keyboard(session))
        return
    await _edit_progress(progress, "Проверил варианты, но ничего подходящего не нашёл.")
    if await send_smart_results(message, query, show_no_results=False):
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
        settings.smtp_provider_normalized,
        settings.smtp_effective_host or "disabled",
        settings.smtp_effective_port,
        _mask_sender(settings.smtp_from_email),
    )
    logger.info("startup database_path=%s search_limit=%s kindle_enabled=%s kindle_queue=%s/%s attachment_mb=%s",settings.database_path,settings.search_results_limit,'yes' if settings.smtp_config_present else 'no',settings.kindle_worker_concurrency,settings.kindle_user_concurrency,settings.kindle_max_attachment_mb)


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



async def _preferred_format(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    pref=await user_preferences_repo.get(user_id)
    return pref.preferred_download_format if pref else None


async def _remember_preferred_format(user_id: int | None, fmt: str) -> None:
    if user_id is None:
        return
    await user_preferences_repo.upsert(user_id, download_format=fmt)


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
    kb.row(InlineKeyboardButton(text="Искать короче",callback_data=f"retry_short:{sid}"))
    kb.row(InlineKeyboardButton(text="Искать как книгу",callback_data=f"retry_book:{sid}"),InlineKeyboardButton(text="Искать как автора",callback_data=f"retry_author:{sid}"))
    kb.row(InlineKeyboardButton(text="🏠 В меню",callback_data="home"))
    await telegram_retry(lambda: message.answer(f"<b>Ничего не нашёл</b>\nЗапрос: <b>{escape(query)}</b>\n\nМожно попробовать короче или искать как автора.",reply_markup=kb.as_markup()))

def _dedupe_books_preserving_order(items):
    seen=set(); result=[]
    for item in items:
        key=(item.book_id, item.title, item.author)
        if key not in seen: seen.add(key); result.append(item)
    return result

def _dedupe_authors_preserving_order(items):
    seen=set(); result=[]
    for item in items:
        if item.author_id not in seen: seen.add(item.author_id); result.append(item)
    return result

def _interleave_book_groups(groups):
    result=[]; seen=set(); width=max((len(g) for g in groups),default=0)
    for index in range(width):
        for group in groups:
            if index >= len(group): continue
            item=group[index]; key=(item.book_id,item.title,item.author)
            if key not in seen: seen.add(key); result.append(item)
    return result

def _recommendation_fallback_queries(query:str)->list[str]:
    q=_norm(query)
    if "российск" in q and "постмодерн" in q: return ["Пелевин","Сорокин","Венедикт Ерофеев"]
    if "зарубеж" in q and "постмодерн" in q: return ["Пол Остер","Харуки Мураками","Марк Данилевский"]
    return []

def _tavily_configured() -> bool:
    return settings.discovery_web_active

def _truncate(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"

def _intent_handler(kind: IntentKind) -> str:
    return {
        IntentKind.EXACT_SEARCH: "send_smart_results",
        IntentKind.AUTHOR_SEARCH: "send_author_results",
        IntentKind.AUTHOR_TITLE_SEARCH: "send_author_title_results",
        IntentKind.RECOMMENDATION: "send_ai_results",
        IntentKind.DISCOVERY_OPTIONAL: "discovery_recommender",
        IntentKind.UNKNOWN_FALLBACK: "fallback",
    }[kind]

async def _send_weak_recommendation(message:Message,query:str)->None:
    sid=uuid4().hex[:10]; retry_sessions[sid]=query; _prune_sessions(retry_sessions)
    kb=InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Попробовать шире",callback_data=f"retry_wide:{sid}"))
    kb.row(InlineKeyboardButton(text="Обычный поиск",callback_data=f"retry_book:{sid}"),InlineKeyboardButton(text="Искать авторов",callback_data=f"retry_author:{sid}"))
    await message.answer("Я не нашёл достаточно точных совпадений. Могу попробовать шире или выполнить обычный поиск.",reply_markup=kb.as_markup())

async def _edit_progress(message: Message, text: str) -> None:
    try:
        await telegram_retry(lambda: message.edit_text(text), attempts=2)
    except Exception:
        logger.debug("Could not edit AI progress message", exc_info=True)

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

async def _notify_admins_about_request(bot: Bot, user: User) -> None:
    kb=InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Разрешить",callback_data=f"access_approve:{user.id}"),InlineKeyboardButton(text="❌ Отклонить",callback_data=f"access_reject:{user.id}"))
    label=escape(user.full_name)
    if user.username: label += f" @{escape(user.username)}"
    for admin_id in settings.admin_ids:
        await telegram_retry(lambda admin_id=admin_id: bot.send_message(admin_id,f"Запрос доступа:\n{label}\n<code>{user.id}</code>",reply_markup=kb.as_markup()))



def _assistant_ui_enabled() -> bool:
    return settings.ai_enabled or settings.discovery_enabled

def _assistant_bot_commands() -> list[BotCommand]:
    commands = [BotCommand(command="recommend", description="подобрать книгу")]
    if settings.discovery_enabled:
        commands.extend([
            BotCommand(command="discover", description="подборка с веб-поиском"),
            BotCommand(command="discover_web", description="явный веб-поиск"),
        ])
    return commands

async def setup_bot_commands(bot: Bot) -> None:
    if settings.ui_hide_command_menu_for_users and not settings.ui_show_power_user_commands:
        await bot.set_my_commands([], scope=BotCommandScopeDefault())
    else:
        await bot.set_my_commands(_power_user_bot_commands(), scope=BotCommandScopeDefault())

    if not settings.ui_show_admin_commands:
        return
    admin_commands = _admin_bot_commands()
    for admin_id in settings.admin_ids:
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))


def _power_user_bot_commands() -> list[BotCommand]:
    commands = [
        BotCommand(command="start", description="открыть меню"),
        BotCommand(command="help", description="помощь"),
        BotCommand(command="search", description="поиск книг"),
        BotCommand(command="author", description="поиск авторов"),
        BotCommand(command="kindle", description="меню Kindle"),
        BotCommand(command="favorites", description="избранное"),
        BotCommand(command="history", description="история"),
        BotCommand(command="last", description="последняя книга"),
    ]
    if _assistant_ui_enabled():
        commands.extend(_assistant_bot_commands())
    return commands


def _admin_bot_commands() -> list[BotCommand]:
    commands = [
        BotCommand(command="admin", description="админка"),
        BotCommand(command="admin_kindle_health", description="Kindle health"),
        BotCommand(command="admin_intent", description="проверить intent"),
    ]
    if settings.discovery_enabled:
        commands.append(BotCommand(command="admin_discovery_status", description="discovery status"))
    return commands


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
            smtp_host=settings.smtp_effective_host,
            smtp_port=settings.smtp_effective_port,
            smtp_starttls=settings.smtp_effective_starttls,
            smtp_provider=settings.smtp_provider_normalized,
            smtp_username=settings.smtp_username,
            smtp_sender_domain=settings.smtp_sender_domain,
            smtp_config_present=settings.smtp_config_present,
            email_sender=email_sender,
            default_format=settings.kindle_default_format,
            max_attachment_mb=settings.kindle_max_attachment_mb,
            admin_user_ids=settings.admin_ids,
            retention_days=settings.kindle_delivery_log_retention_days,
            export_include_full_emails=settings.admin_export_include_full_emails,
            cover_lookup_enabled=settings.cover_lookup_enabled,
            cover_provider_order=settings.cover_provider_order,
            cover_cache_ttl_seconds=settings.cover_cache_ttl_seconds,
            google_books_key_configured=bool(settings.google_books_api_key),
            metadata_polish_enabled=settings.kindle_metadata_polish_enabled,
            metadata_tool=settings.kindle_metadata_tool,
            metadata_tool_available=ebook_metadata_polisher.tool_available(),
            embed_cover_enabled=settings.kindle_embed_cover_enabled,
            kindle_worker_concurrency=settings.kindle_worker_concurrency,
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
