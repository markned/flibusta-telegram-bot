from __future__ import annotations

import logging
from asyncio import sleep
from html import escape
from time import monotonic
from typing import Awaitable, Callable, TypeVar
from urllib.parse import urlparse

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
from app.flibusta import BookDetails, FlibustaClient, FlibustaError

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
            "Отправь название книги или автора. Еще можно использовать /search &lt;запрос&gt;."
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
        results = await flibusta.search(query)
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

    keyboard = InlineKeyboardBuilder()
    for item in results:
        label = item.title if not item.author else f"{item.title} - {item.author}"
        keyboard.row(InlineKeyboardButton(text=label[:64], callback_data=f"book:{item.book_id}"))

    log_user_action(
        message.from_user,
        message.chat.id,
        "search_ok",
        query=query,
        results=len(results),
        duration=elapsed(started_at),
    )
    await telegram_retry(lambda: message.answer("Нашел варианты:", reply_markup=keyboard.as_markup()))


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
        parts.append("Автор: " + escape(", ".join(details.authors[:5])))
    if details.annotation:
        parts.append(escape(details.annotation))
    if not details.formats:
        parts.append("Доступные форматы не найдены.")
    return "\n\n".join(parts)


def _formats_keyboard(details: BookDetails):
    if not details.formats:
        return None

    keyboard = InlineKeyboardBuilder()
    for item in details.formats:
        keyboard.button(text=item.label, callback_data=f"dl:{details.book_id}:{item.code}")
    keyboard.adjust(3)
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
