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
from app.messages.kindle import kindle_setup_text, kindle_missing_email_text
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

    @router.message(Command("kindle_email"))
    async def kindle_email(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if not raw:
            await message.answer("Use: <code>/kindle_email your_name@kindle.com</code>")
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
        await message.answer(f"Kindle e-mail saved: {mask_email(normalized)}")

    @router.message(Command("kindle_help"))
    async def kindle_help(message: Message) -> None:
        sender = smtp_from_email or "not configured by admin yet"
        await message.answer(
            "Send to Kindle setup:\n"
            "1. Find your Kindle e-mail in Amazon Kindle settings.\n"
            f"2. Add <code>{escape(sender)}</code> to Amazon Approved Personal Document E-mail List.\n"
            "3. Save your Kindle e-mail with <code>/kindle_email your_name@kindle.com</code>.\n"
            "4. Use the «📤 Send to Kindle» button in a book card.\n\n"
            f"The sender address Amazon must approve is: <code>{escape(sender)}</code>."
        )
    @router.message(Command("kindle_setup"))
    async def kindle_setup(message: Message) -> None:
        await message.answer(kindle_setup_text(smtp_from_email))

    @router.message(Command("kindle_status"))
    async def kindle_status(message: Message) -> None:
        settings = await settings_repo.get(message.from_user.id)
        if settings is None:
            await message.answer(
                "Kindle e-mail is not configured yet. Use "
                "<code>/kindle_email your_name@kindle.com</code>."
            )
            return
        sender = smtp_from_email or "not configured"
        await message.answer(
            f"Kindle e-mail: {mask_email(settings.kindle_email)}\n"
            f"Preferred format: {escape(settings.preferred_kindle_format)}\n"
            f"Amazon-approved sender: <code>{escape(sender)}</code>"
        )

    @router.message(Command("kindle_remove"))
    async def kindle_remove(message: Message) -> None:
        await settings_repo.delete(message.from_user.id)
        await message.answer("Kindle e-mail removed.")

    @router.message(Command("kindle_format"))
    async def kindle_format(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip().lower()
        settings = await settings_repo.get(message.from_user.id)
        current = settings.preferred_kindle_format if settings else default_format
        if not raw:
            await message.answer(
                f"Current Kindle format: <b>{escape(current)}</b>\n"
                "Allowed: epub, fb2, txt, pdf.\n"
                "EPUB is recommended for Kindle."
            )
            return
        if raw not in ALLOWED_KINDLE_FORMATS:
            await message.answer("Allowed Kindle formats: epub, fb2, txt, pdf. EPUB is recommended.")
            return
        if settings is None:
            await message.answer(
                "Kindle e-mail is not configured yet. Use "
                "<code>/kindle_email your_name@kindle.com</code> first."
            )
            return
        await settings_repo.update_preferred_format(message.from_user.id, raw)
        await preferences_repo.upsert(message.from_user.id, kindle_format=raw)
        await message.answer(f"Preferred Kindle format saved: {escape(raw)}. EPUB is recommended.")

    @router.message(Command("kindle_history"))
    async def kindle_history(message: Message) -> None:
        items = await deliveries_repo.get_recent_for_user(message.from_user.id, limit=10)
        if not items:
            await message.answer("No Kindle deliveries yet.")
            return
        await message.answer(format_history(items))
    @router.message(Command("kindle_retry"))
    async def kindle_retry(message: Message, command: CommandObject) -> None:
        arg=(command.args or '').strip()
        old=await (deliveries_repo.get_by_id(int(arg)) if arg.isdigit() else deliveries_repo.get_latest_failed_for_user(message.from_user.id))
        if old is None or old.user_id != message.from_user.id:
            await message.answer("No failed Kindle delivery found to retry."); return
        if old.status != 'failed':
            await message.answer("Only failed deliveries can be retried."); return
        status=await message.answer("Queued for Kindle…")
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
        await message.answer("\\n".join(f"{d.id} user={d.user_id} {d.title or d.book_id} [{d.format or '?'}] failed category={(d.last_error or d.error or 'unknown').split(':',1)[0]} {d.created_at[:16]}" for d in items))
    @router.message(Command("admin_kindle_delivery"))
    async def admin_kindle_delivery(message:Message, command:CommandObject)->None:
        if message.from_user.id not in admin_user_ids:return
        arg=(command.args or '').strip(); d=await deliveries_repo.get_by_id(int(arg)) if arg.isdigit() else None
        if not d: await message.answer("Delivery not found."); return
        await message.answer(f"id={d.id}\\nuser_id={d.user_id}\\nbook_id={d.book_id}\\ntitle={d.title}\\nformat={d.format}\\nfilename={d.filename}\\nsize={d.file_size_bytes}\\nstatus={d.status}\\nattempts={d.attempts}\\ncreated_at={d.created_at}\\nupdated_at={d.updated_at}\\nlast_error={_short_error(d.last_error or d.error or '')}")
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
            kb=InlineKeyboardBuilder(); kb.row(InlineKeyboardButton(text="How to set up Kindle",callback_data="kindle_setup_help"),InlineKeyboardButton(text="Show sender e-mail",callback_data="kindle_sender"))
            await callback.message.answer(kindle_missing_email_text(),reply_markup=kb.as_markup())
            return
        await callback.answer("Added to Kindle queue")
        status = await callback.message.answer("Queued for Kindle…")
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
    async def sender_cb(callback:CallbackQuery): await callback.answer(); await callback.message.answer(smtp_from_email or "Kindle sending is not configured by the bot owner yet.")
    return router


def user_message_for_exception(exc: Exception) -> str:
    if isinstance(exc, KindleSettingsMissingError):
        return "Kindle e-mail is not configured yet. Use /kindle_email your_name@kindle.com"
    if isinstance(exc, KindleFileTooLargeError):
        return "This file is too large to send to Kindle by e-mail. Try another format."
    if isinstance(exc, KindleRateLimitError):
        return "You reached the Kindle sending limit for this hour. Try again later."
    if isinstance(exc, KindleConversionNotAvailableError):
        return "This format is not ready for Kindle delivery yet. Try another format."
    if isinstance(exc,(SMTPAuthenticationError,EmailConfigurationError,SMTPRecipientsRefused,SMTPResponseException)):
        return smtp_user_message(classify_smtp_error(exc))
    return "Failed to send this book to Kindle. Try again later."


def format_history(items: list[KindleDelivery]) -> str:
    lines = ["Last Kindle deliveries:"]
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
