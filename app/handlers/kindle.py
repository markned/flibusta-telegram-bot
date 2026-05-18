from __future__ import annotations

import logging
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosmtplib.errors import SMTPAuthenticationError, SMTPRecipientsRefused, SMTPResponseException

from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository, KindleDelivery
from app.repositories.kindle_settings import KindleSettingsRepository
from app.repositories.user_preferences import UserPreferencesRepository
from app.messages.kindle import kindle_setup_text, kindle_missing_email_text, kindle_home_text
from app.services.email_sender import EmailConfigurationError
from app.services.kindle import (
    KindleConversionNotAvailableError,
    KindleEmailInvalidError,
    KindleFileTooLargeError,
    KindleRateLimitError,
    KindleSettingsMissingError,
    mask_email,
    validate_kindle_email,
)
from app.services.kindle_queue import KindleQueue
from app.services.smtp_errors import classify_smtp_error, smtp_user_message

logger = logging.getLogger(__name__)
ALLOWED_KINDLE_FORMATS = {"epub", "fb2", "txt", "pdf"}


def build_kindle_router(
    *,
    db: Database,
    settings_repo: KindleSettingsRepository,
    deliveries_repo: KindleDeliveriesRepository,
    preferences_repo: UserPreferencesRepository,
    kindle_queue: KindleQueue,
    smtp_from_email: str | None,
    smtp_host: str | None,
    smtp_port: int,
    smtp_config_present: bool,
    default_format: str,
    max_attachment_mb: int,
    admin_user_ids: set[int],
    retention_days: int,
    export_include_full_emails: bool,
) -> Router:
    router = Router()

    async def send_kindle_home(message: Message, user_id: int | None = None) -> None:
        settings = await settings_repo.get(user_id or message.from_user.id)
        await message.answer(kindle_home_text(settings, smtp_from_email), reply_markup=_kindle_home_keyboard(settings is not None))

    @router.message(Command("kindle_email"))
    async def kindle_email(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if not raw:
            await message.answer("Сохрани адрес так:\n<code>/kindle_email your_name@kindle.com</code>")
            return
        try:
            normalized = validate_kindle_email(raw)
        except KindleEmailInvalidError as exc:
            await message.answer(str(exc))
            return
        current = await settings_repo.get(message.from_user.id)
        preferred = current.preferred_kindle_format if current else default_format
        await settings_repo.upsert(message.from_user.id, normalized, preferred_format=preferred)
        await preferences_repo.upsert(message.from_user.id, kindle_format=preferred)
        await message.answer(f"Kindle-адрес сохранён: {mask_email(normalized)}")
        await send_kindle_home(message)

    @router.message(Command("kindle_help"))
    async def kindle_help(message: Message) -> None:
        await message.answer(kindle_setup_text(smtp_from_email))
    @router.message(Command("kindle_setup"))
    async def kindle_setup(message: Message) -> None:
        await message.answer(kindle_setup_text(smtp_from_email))

    @router.message(Command("kindle"))
    async def kindle_home(message: Message) -> None:
        await send_kindle_home(message)
    @router.message(F.text == "⚙️ Kindle")
    async def kindle_home_button(message: Message) -> None:
        await send_kindle_home(message)

    @router.message(Command("kindle_status"))
    async def kindle_status(message: Message) -> None:
        settings = await settings_repo.get(message.from_user.id)
        if settings is None:
            await send_kindle_home(message)
            return
        await send_kindle_home(message)

    @router.message(Command("kindle_remove"))
    async def kindle_remove(message: Message) -> None:
        await settings_repo.delete(message.from_user.id)
        await message.answer("Kindle-адрес удалён.")
        await send_kindle_home(message)

    @router.message(Command("kindle_format"))
    async def kindle_format(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip().lower()
        settings = await settings_repo.get(message.from_user.id)
        current = settings.preferred_kindle_format if settings else default_format
        if not raw:
            await message.answer(
                f"Текущий формат Kindle: <b>{escape(current.upper())}</b>\n"
                "Доступны: epub, fb2, txt, pdf.\n"
                "Для Kindle лучше всего EPUB."
            )
            return
        if raw not in ALLOWED_KINDLE_FORMATS:
            await message.answer("Доступны форматы: epub, fb2, txt, pdf. Для Kindle лучше EPUB.")
            return
        if settings is None:
            await message.answer(
                "Сначала настрой Kindle-адрес: "
                "<code>/kindle_email your_name@kindle.com</code>"
            )
            return
        await settings_repo.update_preferred_format(message.from_user.id, raw)
        await preferences_repo.upsert(message.from_user.id, kindle_format=raw)
        await message.answer(f"Формат Kindle сохранён: {escape(raw.upper())}.")

    @router.message(Command("kindle_history"))
    async def kindle_history(message: Message) -> None:
        items = await deliveries_repo.get_recent_for_user(message.from_user.id, limit=10)
        if not items:
            await message.answer("Отправок на Kindle пока не было.")
            return
        await message.answer(format_history(items))
    @router.message(Command("kindle_retry"))
    async def kindle_retry(message: Message, command: CommandObject) -> None:
        arg=(command.args or '').strip()
        old=await (deliveries_repo.get_by_id(int(arg)) if arg.isdigit() else deliveries_repo.get_latest_failed_for_user(message.from_user.id))
        if old is None or old.user_id != message.from_user.id:
            await message.answer("Не нашёл неудачную отправку для повтора."); return
        if old.status != 'failed':
            await message.answer("Повторять можно только неудачные отправки."); return
        status=await message.answer("Добавил в очередь Kindle…")
        try: await kindle_queue.enqueue(user_id=old.user_id,chat_id=message.chat.id,book_id=old.book_id,status_message_id=status.message_id,retry_of_delivery_id=old.id)
        except Exception as exc: await status.edit_text(user_message_for_exception(exc))

    @router.message(Command("admin_kindle_health"))
    async def admin_kindle_health(message: Message) -> None:
        if message.from_user.id not in admin_user_ids:
            return
        sqlite_ok = await db.ping()
        failures = await deliveries_repo.count_recent_failures(hours=24)
        sender = smtp_from_email or "not configured"
        await message.answer(
            "Kindle health\n"
            f"SQLite reachable: {'yes' if sqlite_ok else 'no'}\n"
            f"SMTP config present: {'yes' if smtp_config_present else 'no'}\n"
            f"SMTP host: {escape(smtp_host or 'not configured')}\n"
            f"SMTP port: {smtp_port}\n"
            f"SMTP from: <code>{escape(mask_email(sender))}</code>\n"
            f"Max attachment: {max_attachment_mb} MB\n"
            f"Queue size: {kindle_queue.size}\n"
            f"Active jobs: {kindle_queue.active_jobs}\n"
            f"Recent failures (24h): {failures}"
        )
    @router.message(Command("admin_kindle_failures"))
    async def admin_kindle_failures(message: Message)->None:
        if message.from_user.id not in admin_user_ids:return
        items=await deliveries_repo.get_recent_failures()
        if not items: await message.answer("No recent Kindle failures."); return
        await message.answer("\n".join(f"{d.id} user={d.user_id} {d.title or d.book_id} [{d.format or '?'}] failed category={(d.last_error or d.error or 'unknown').split(':',1)[0]} {d.created_at[:16]}" for d in items))
    @router.message(Command("admin_kindle_delivery"))
    async def admin_kindle_delivery(message:Message, command:CommandObject)->None:
        if message.from_user.id not in admin_user_ids:return
        arg=(command.args or '').strip(); d=await deliveries_repo.get_by_id(int(arg)) if arg.isdigit() else None
        if not d: await message.answer("Delivery not found."); return
        await message.answer(f"id={d.id}\nuser_id={d.user_id}\nbook_id={d.book_id}\ntitle={d.title}\nformat={d.format}\nfilename={d.filename}\nsize={d.file_size_bytes}\nstatus={d.status}\nattempts={d.attempts}\ncreated_at={d.created_at}\nupdated_at={d.updated_at}\nlast_error={_short_error(d.last_error or d.error or '')}")
    @router.message(Command("admin_cleanup_deliveries"))
    async def admin_cleanup(message:Message)->None:
        if message.from_user.id not in admin_user_ids:return
        count=await deliveries_repo.cleanup_completed(retention_days); await message.answer(f"Deleted {count} old delivery records.")
    @router.message(Command("admin_export_settings"))
    async def admin_export(message:Message)->None:
        if message.from_user.id not in admin_user_ids:return
        rows=await preferences_repo.all_rows(); out=[]
        for r in rows:
            ks=await settings_repo.get(r['user_id']); deliveries=await deliveries_repo.get_recent_for_user(r['user_id'],limit=100000)
            out.append({'user_id':r['user_id'],'kindle_email':(ks.kindle_email if ks and export_include_full_emails else mask_email(ks.kindle_email if ks else None)),'preferred_download_format':r['preferred_download_format'],'preferred_kindle_format':r['preferred_kindle_format'],'created_at':r['created_at'],'updated_at':r['updated_at'],'delivery_count':len(deliveries)})
        import json
        await message.answer_document(BufferedInputFile(json.dumps(out,ensure_ascii=False,indent=2).encode(),'settings.json'))

    @router.callback_query(F.data.startswith("kindle:"))
    async def send_to_kindle(callback: CallbackQuery) -> None:
        book_id = callback.data.split(":", 1)[1]
        settings = await settings_repo.get(callback.from_user.id)
        if settings is None:
            await callback.answer()
            kb=InlineKeyboardBuilder(); kb.row(InlineKeyboardButton(text="Настроить Kindle",callback_data="kindle_setup_help"),InlineKeyboardButton(text="Показать отправителя",callback_data="kindle_sender"))
            await callback.message.answer(kindle_missing_email_text(),reply_markup=kb.as_markup())
            return
        await callback.answer("Добавил в очередь Kindle")
        status = await callback.message.answer("Добавил в очередь Kindle…")
        try:
            await kindle_queue.enqueue(
                user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                book_id=book_id,
                status_message_id=status.message_id,
            )
        except Exception as exc:
            logger.exception("Failed to enqueue Kindle job")
            await status.edit_text(user_message_for_exception(exc))

    @router.callback_query(F.data=="kindle_setup_help")
    async def setup_help_cb(callback:CallbackQuery): await callback.answer(); await callback.message.answer(kindle_setup_text(smtp_from_email))
    @router.callback_query(F.data=="kindle_sender")
    async def sender_cb(callback:CallbackQuery): await callback.answer(); await callback.message.answer(smtp_from_email or "Отправка на Kindle пока не настроена владельцем бота.")
    @router.callback_query(F.data=="kindle_home")
    async def home_cb(callback:CallbackQuery): await callback.answer(); await send_kindle_home(callback.message, callback.from_user.id)
    @router.callback_query(F.data=="kindle_history_home")
    async def history_cb(callback:CallbackQuery): await callback.answer(); items=await deliveries_repo.get_recent_for_user(callback.from_user.id,limit=10); await callback.message.answer(format_history(items) if items else "Отправок на Kindle пока не было.")
    @router.callback_query(F.data.startswith("kindle_fmt:"))
    async def fmt_cb(callback:CallbackQuery):
        await callback.answer()
        value=callback.data.split(":",1)[1]; settings=await settings_repo.get(callback.from_user.id)
        if settings is None: await callback.message.answer(kindle_missing_email_text()); return
        await settings_repo.update_preferred_format(callback.from_user.id,value); await preferences_repo.upsert(callback.from_user.id,kindle_format=value); await callback.message.answer(f"Формат Kindle сохранён: {value.upper()}.")
    return router


def user_message_for_exception(exc: Exception) -> str:
    if isinstance(exc, KindleSettingsMissingError):
        return "Kindle пока не настроен. Нажми ⚙️ Kindle и сохрани адрес."
    if isinstance(exc, KindleFileTooLargeError):
        return "Файл слишком большой для отправки на Kindle. Попробуй другой формат."
    if isinstance(exc, KindleRateLimitError):
        return "Лимит отправок на Kindle на этот час исчерпан. Попробуй позже."
    if isinstance(exc, KindleConversionNotAvailableError):
        return "Этот формат пока не готов для Kindle. Попробуй другой."
    if isinstance(exc,(SMTPAuthenticationError,EmailConfigurationError,SMTPRecipientsRefused,SMTPResponseException)):
        return smtp_user_message(classify_smtp_error(exc))
    return "Не удалось отправить книгу на Kindle. Попробуй позже."


def format_history(items: list[KindleDelivery]) -> str:
    lines = ["Последние отправки на Kindle:"]
    for item in items:
        stamp = _short_datetime(item.created_at)
        title = item.title or f"book {item.book_id}"
        format_label = item.format or "?"
        line = f"{stamp} — {title} [{format_label}] — {item.status}"
        if item.status == "failed" and item.error:
            line += f" ({_short_error(item.error)})"
        lines.append(line)
    return "\n".join(lines)


def _short_datetime(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


def _short_error(value: str, limit: int = 80) -> str:
    clean = " ".join(value.split())
    return clean[: limit - 3] + "..." if len(clean) > limit else clean

def _kindle_home_keyboard(configured: bool):
    kb=InlineKeyboardBuilder()
    if not configured:
        kb.row(InlineKeyboardButton(text="Как настроить",callback_data="kindle_setup_help"),InlineKeyboardButton(text="Отправитель",callback_data="kindle_sender"))
    else:
        kb.row(InlineKeyboardButton(text="История",callback_data="kindle_history_home"),InlineKeyboardButton(text="Как настроить",callback_data="kindle_setup_help"))
        kb.row(InlineKeyboardButton(text="EPUB",callback_data="kindle_fmt:epub"),InlineKeyboardButton(text="FB2",callback_data="kindle_fmt:fb2"),InlineKeyboardButton(text="TXT",callback_data="kindle_fmt:txt"))
    return kb.as_markup()
