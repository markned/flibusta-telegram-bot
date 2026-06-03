from __future__ import annotations

import json
import logging
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosmtplib.errors import SMTPAuthenticationError, SMTPRecipientsRefused, SMTPResponseException

from app.messages.kindle import kindle_home_text, kindle_missing_email_text, kindle_setup_text
from app.repositories.db import Database
from app.repositories.kindle_deliveries import KindleDeliveriesRepository, KindleDelivery
from app.repositories.kindle_settings import KindleSettingsRepository, KindleSettings
from app.repositories.user_preferences import UserPreferencesRepository
from app.services.email_sender import EmailConfigurationError, EmailSender, mask_smtp_identity
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


class KindleEmailForm(StatesGroup):
    waiting_for_email = State()


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
    smtp_starttls: bool,
    smtp_provider: str,
    smtp_username: str | None,
    smtp_sender_domain: str | None,
    smtp_config_present: bool,
    email_sender: EmailSender,
    default_format: str,
    max_attachment_mb: int,
    admin_user_ids: set[int],
    retention_days: int,
    export_include_full_emails: bool,
    cover_lookup_enabled: bool = False,
    cover_provider_order: str = "",
    cover_cache_ttl_seconds: int = 0,
    google_books_key_configured: bool = False,
    metadata_polish_enabled: bool = False,
    metadata_tool: str = "ebook-meta",
    metadata_tool_available: bool = False,
    embed_cover_enabled: bool = False,
    kindle_worker_concurrency: int = 1,
) -> Router:
    router = Router()

    async def _save_kindle_email(user_id: int, raw: str) -> KindleSettings:
        normalized = validate_kindle_email(raw.strip())
        current = await settings_repo.get(user_id)
        preferred = current.preferred_kindle_format if current else default_format
        settings = await settings_repo.upsert(user_id, normalized, preferred_format=preferred)
        await preferences_repo.upsert(user_id, kindle_format=preferred)
        return settings

    async def send_kindle_home(message: Message, user_id: int | None = None) -> None:
        settings = await settings_repo.get(user_id or message.from_user.id)
        await message.answer(
            kindle_home_text(settings, smtp_from_email, smtp_provider),
            reply_markup=_kindle_home_keyboard(settings),
        )

    @router.message(Command("kindle_email"))
    async def kindle_email(message: Message, command: CommandObject, state: FSMContext) -> None:
        raw = (command.args or "").strip()
        if not raw:
            await state.set_state(KindleEmailForm.waiting_for_email)
            await message.answer(
                "Пришли свой Kindle e-mail одним сообщением.\n"
                "Например: <code>your_name@kindle.com</code>",
                reply_markup=_kindle_email_input_keyboard(),
            )
            return
        try:
            settings = await _save_kindle_email(message.from_user.id, raw)
        except KindleEmailInvalidError as exc:
            await message.answer(str(exc), reply_markup=_kindle_email_input_keyboard())
            return
        await message.answer(f"Kindle e-mail сохранён: {mask_email(settings.kindle_email)}")
        await send_kindle_home(message)

    @router.message(StateFilter(KindleEmailForm.waiting_for_email))
    async def kindle_email_input(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        if raw.lower() in {"/cancel", "отмена", "cancel"}:
            await state.clear()
            await message.answer("Ок, отменил.")
            return
        try:
            settings = await _save_kindle_email(message.from_user.id, raw)
        except KindleEmailInvalidError as exc:
            await message.answer(
                f"Не похоже на Kindle e-mail. {escape(str(exc))}\n\n"
                "Нужен адрес на <code>@kindle.com</code> или <code>@free.kindle.com</code>.",
                reply_markup=_kindle_email_input_keyboard(),
            )
            return
        await state.clear()
        await message.answer(f"Готово, сохранил: {mask_email(settings.kindle_email)}")
        await send_kindle_home(message)

    @router.message(Command("kindle_help"))
    async def kindle_help(message: Message) -> None:
        await message.answer(kindle_setup_text(smtp_from_email, smtp_provider), reply_markup=_kindle_setup_keyboard())

    @router.message(Command("kindle_setup"))
    async def kindle_setup(message: Message) -> None:
        await message.answer(kindle_setup_text(smtp_from_email, smtp_provider), reply_markup=_kindle_setup_keyboard())

    @router.message(Command("kindle"))
    async def kindle_home(message: Message) -> None:
        await send_kindle_home(message)

    @router.message(F.text == "⚙️ Kindle")
    async def kindle_home_button(message: Message) -> None:
        await send_kindle_home(message)

    @router.message(Command("kindle_status"))
    async def kindle_status(message: Message) -> None:
        await send_kindle_home(message)

    @router.message(Command("kindle_remove"))
    async def kindle_remove(message: Message) -> None:
        await settings_repo.delete(message.from_user.id)
        await message.answer("Kindle e-mail удалён.")
        await send_kindle_home(message)

    @router.message(Command("kindle_format"))
    async def kindle_format(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip().lower()
        settings = await settings_repo.get(message.from_user.id)
        current = settings.preferred_kindle_format if settings else default_format
        if not raw:
            await message.answer(
                f"Текущий формат Kindle: <b>{escape(current.upper())}</b>\n"
                "Доступны: epub, fb2, txt, pdf. Для Kindle обычно лучше EPUB.",
                reply_markup=_kindle_format_keyboard(current),
            )
            return
        if raw not in ALLOWED_KINDLE_FORMATS:
            await message.answer("Доступны форматы: epub, fb2, txt, pdf. Для Kindle лучше EPUB.")
            return
        if settings is None:
            await message.answer(kindle_missing_email_text(), reply_markup=_kindle_home_keyboard(None))
            return
        await settings_repo.update_preferred_format(message.from_user.id, raw)
        await preferences_repo.upsert(message.from_user.id, kindle_format=raw)
        await message.answer(f"Формат Kindle сохранён: {escape(raw.upper())}.")
        await send_kindle_home(message)

    @router.message(Command("kindle_history"))
    async def kindle_history(message: Message) -> None:
        items = await deliveries_repo.get_recent_for_user(message.from_user.id, limit=10)
        if not items:
            await message.answer("Отправок на Kindle пока не было.")
            return
        await message.answer(format_history(items))

    @router.message(Command("kindle_retry"))
    async def kindle_retry(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip()
        old = await (
            deliveries_repo.get_by_id(int(arg))
            if arg.isdigit()
            else deliveries_repo.get_latest_failed_for_user(message.from_user.id)
        )
        if old is None or old.user_id != message.from_user.id:
            await message.answer("Не нашёл неудачную отправку для повтора.")
            return
        if old.status != "failed":
            await message.answer("Повторять можно только неудачные отправки.")
            return
        status = await message.answer("Добавил в очередь Kindle…")
        try:
            await kindle_queue.enqueue(
                user_id=old.user_id,
                chat_id=message.chat.id,
                book_id=old.book_id,
                status_message_id=status.message_id,
                retry_of_delivery_id=old.id,
            )
        except Exception as exc:
            await status.edit_text(user_message_for_exception(exc))

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
            f"SMTP provider: {escape(smtp_provider)}\n"
            f"SMTP config present: {'yes' if smtp_config_present else 'no'}\n"
            f"SMTP host: {escape(smtp_host or 'not configured')}\n"
            f"SMTP port: {smtp_port}\n"
            f"STARTTLS: {'yes' if smtp_starttls else 'no'}\n"
            f"SMTP username: <code>{escape(mask_smtp_identity(smtp_username))}</code>\n"
            f"SMTP from: <code>{escape(mask_email(sender))}</code>\n"
            f"Sender domain: {escape(smtp_sender_domain or 'not configured')}\n"
            f"Max attachment: {max_attachment_mb} MB\n"
            f"Queue size: {kindle_queue.size}\n"
            f"Active jobs: {kindle_queue.active_jobs}\n"
            f"Recent failures (24h): {failures}\n"
            f"Cover lookup: {'yes' if cover_lookup_enabled else 'no'}\n"
            f"Cover providers: {escape(cover_provider_order or 'disabled')}\n"
            f"Google Books key: {'yes' if google_books_key_configured else 'no'}\n"
            f"Cover cache TTL: {cover_cache_ttl_seconds}s\n"
            f"Metadata polish: {'yes' if metadata_polish_enabled else 'no'}\n"
            f"Metadata tool: {escape(metadata_tool)} ({'available' if metadata_tool_available else 'missing'})\n"
            f"Embed cover: {'yes' if embed_cover_enabled else 'no'}\n"
            f"Kindle worker concurrency: {kindle_worker_concurrency}"
            + ("\nGmail SMTP requires a Google app password." if smtp_provider == "gmail" else "")
            + ("\nGoogle Workspace sender/DNS is configured outside the bot." if smtp_provider == "google_workspace" else "")
        )

    @router.message(Command("admin_kindle_failures"))
    async def admin_kindle_failures(message: Message) -> None:
        if message.from_user.id not in admin_user_ids:
            return
        items = await deliveries_repo.get_recent_failures()
        if not items:
            await message.answer("No recent Kindle failures.")
            return
        await message.answer(
            "\n".join(
                f"{d.id} user={d.user_id} {d.title or d.book_id} [{d.format or '?'}] "
                f"failed category={(d.last_error or d.error or 'unknown').split(':', 1)[0]} {d.created_at[:16]}"
                for d in items
            )
        )

    @router.message(Command("admin_kindle_delivery"))
    async def admin_kindle_delivery(message: Message, command: CommandObject) -> None:
        if message.from_user.id not in admin_user_ids:
            return
        arg = (command.args or "").strip()
        d = await deliveries_repo.get_by_id(int(arg)) if arg.isdigit() else None
        if not d:
            await message.answer("Delivery not found.")
            return
        await message.answer(
            f"id={d.id}\nuser_id={d.user_id}\nbook_id={d.book_id}\ntitle={d.title}\n"
            f"format={d.format}\nfilename={d.filename}\nsize={d.file_size_bytes}\nstatus={d.status}\n"
            f"attempts={d.attempts}\ncreated_at={d.created_at}\nupdated_at={d.updated_at}\n"
            f"last_error={_short_error(d.last_error or d.error or '')}"
        )

    @router.message(Command("admin_cleanup_deliveries"))
    async def admin_cleanup(message: Message) -> None:
        if message.from_user.id not in admin_user_ids:
            return
        count = await deliveries_repo.cleanup_completed(retention_days)
        await message.answer(f"Deleted {count} old delivery records.")

    @router.message(Command("admin_export_settings"))
    async def admin_export(message: Message) -> None:
        if message.from_user.id not in admin_user_ids:
            return
        rows = await preferences_repo.all_rows()
        out = []
        for r in rows:
            ks = await settings_repo.get(r["user_id"])
            deliveries = await deliveries_repo.get_recent_for_user(r["user_id"], limit=100000)
            out.append(
                {
                    "user_id": r["user_id"],
                    "kindle_email": (
                        ks.kindle_email
                        if ks and export_include_full_emails
                        else mask_email(ks.kindle_email if ks else None)
                    ),
                    "preferred_download_format": r["preferred_download_format"],
                    "preferred_kindle_format": r["preferred_kindle_format"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "delivery_count": len(deliveries),
                }
            )
        await message.answer_document(BufferedInputFile(json.dumps(out, ensure_ascii=False, indent=2).encode(), "settings.json"))

    @router.callback_query(F.data.startswith("kindle:"))
    async def send_to_kindle(callback: CallbackQuery) -> None:
        book_id = callback.data.split(":", 1)[1]
        settings = await settings_repo.get(callback.from_user.id)
        if settings is None:
            await callback.answer()
            await callback.message.answer(kindle_missing_email_text(), reply_markup=_kindle_home_keyboard(None))
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

    @router.callback_query(F.data == "kindle_email_edit")
    async def email_edit_cb(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(KindleEmailForm.waiting_for_email)
        await callback.message.answer(
            "Пришли свой Kindle e-mail одним сообщением.\n"
            "Например: <code>your_name@kindle.com</code>",
            reply_markup=_kindle_email_input_keyboard(),
        )

    @router.callback_query(F.data == "kindle_email_cancel")
    async def email_cancel_cb(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer("Ок, отменил.")
        await send_kindle_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "kindle_setup_help")
    async def setup_help_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        await callback.message.answer(kindle_setup_text(smtp_from_email, smtp_provider), reply_markup=_kindle_setup_keyboard())

    @router.callback_query(F.data == "kindle_sender")
    async def sender_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        if smtp_from_email:
            await callback.message.answer(
                "Отправитель бота:\n"
                f"<code>{escape(smtp_from_email)}</code>\n\n"
                "Добавь этот адрес в Amazon Approved Personal Document E-mail List."
            )
        else:
            await callback.message.answer("Отправка на Kindle пока не настроена владельцем бота.")

    @router.callback_query(F.data == "kindle_home")
    async def home_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        await send_kindle_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "kindle_history_home")
    async def history_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        items = await deliveries_repo.get_recent_for_user(callback.from_user.id, limit=10)
        await callback.message.answer(format_history(items) if items else "Отправок на Kindle пока не было.")

    @router.callback_query(F.data == "kindle_format_menu")
    async def format_menu_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        settings = await settings_repo.get(callback.from_user.id)
        current = settings.preferred_kindle_format if settings else default_format
        await callback.message.answer(
            f"Выбери формат для Kindle. Сейчас: <b>{escape(current.upper())}</b>\n"
            "Рекомендую EPUB.",
            reply_markup=_kindle_format_keyboard(current),
        )

    @router.callback_query(F.data.startswith("kindle_fmt:"))
    async def fmt_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        value = callback.data.split(":", 1)[1]
        if value not in ALLOWED_KINDLE_FORMATS:
            await callback.message.answer("Неизвестный формат Kindle.")
            return
        settings = await settings_repo.get(callback.from_user.id)
        if settings is None:
            await callback.message.answer(kindle_missing_email_text(), reply_markup=_kindle_home_keyboard(None))
            return
        await settings_repo.update_preferred_format(callback.from_user.id, value)
        await preferences_repo.upsert(callback.from_user.id, kindle_format=value)
        await callback.message.answer(f"Формат Kindle сохранён: {value.upper()}. EPUB обычно лучше всего подходит для Kindle.")
        await send_kindle_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "kindle_sender_confirmed")
    async def sender_confirmed_cb(callback: CallbackQuery) -> None:
        settings = await settings_repo.set_approved_sender_confirmed(callback.from_user.id, True)
        await callback.answer("Запомнил")
        if settings is None:
            await callback.message.answer(kindle_missing_email_text(), reply_markup=_kindle_home_keyboard(None))
            return
        await callback.message.answer("Отлично. Отмечу, что отправитель добавлен в Amazon.")
        await send_kindle_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "kindle_test")
    async def kindle_test_cb(callback: CallbackQuery) -> None:
        settings = await settings_repo.get(callback.from_user.id)
        if settings is None:
            await callback.answer()
            await callback.message.answer(kindle_missing_email_text(), reply_markup=_kindle_home_keyboard(None))
            return
        if not smtp_config_present:
            await callback.answer()
            await callback.message.answer("Отправка на Kindle пока не настроена владельцем бота.")
            return
        await callback.answer("Отправляю тест")
        status = await callback.message.answer("Отправляю тестовое письмо на Kindle…")
        try:
            await email_sender.send_attachment(
                to_email=settings.kindle_email,
                subject="Kindle test",
                filename="kindle-test.txt",
                content=b"Sent to Kindle by your private library bot.",
                content_type="text/plain",
            )
        except Exception as exc:
            logger.exception("Kindle test e-mail failed error_type=%s", type(exc).__name__)
            await status.edit_text(user_message_for_exception(exc))
            return
        await status.edit_text("Тест отправлен. Если адрес отправителя разрешён в Amazon, он появится на Kindle через несколько минут.")

    @router.callback_query(F.data == "kindle_remove_confirm")
    async def remove_confirm_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="Да, удалить", callback_data="kindle_remove_do"))
        kb.row(InlineKeyboardButton(text="Отмена", callback_data="kindle_home"))
        await callback.message.answer("Удалить сохранённый Kindle e-mail?", reply_markup=kb.as_markup())

    @router.callback_query(F.data == "kindle_remove_do")
    async def remove_do_cb(callback: CallbackQuery) -> None:
        await settings_repo.delete(callback.from_user.id)
        await callback.answer("Удалено")
        await callback.message.answer("Kindle e-mail удалён.")
        await send_kindle_home(callback.message, callback.from_user.id)

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
    if isinstance(exc, (SMTPAuthenticationError, EmailConfigurationError, SMTPRecipientsRefused, SMTPResponseException)):
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


def _kindle_home_keyboard(settings: KindleSettings | None):
    kb = InlineKeyboardBuilder()
    if settings is None:
        kb.row(InlineKeyboardButton(text="📮 Сохранить Kindle e-mail", callback_data="kindle_email_edit"))
        kb.row(
            InlineKeyboardButton(text="📨 Показать отправителя", callback_data="kindle_sender"),
            InlineKeyboardButton(text="❓ Инструкция", callback_data="kindle_setup_help"),
        )
        kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
        return kb.as_markup()

    kb.row(InlineKeyboardButton(text="📮 Изменить Kindle e-mail", callback_data="kindle_email_edit"))
    kb.row(InlineKeyboardButton(text=f"📄 Формат: {settings.preferred_kindle_format.upper()}", callback_data="kindle_format_menu"))
    kb.row(InlineKeyboardButton(text="✅ Я добавил отправителя в Amazon", callback_data="kindle_sender_confirmed"))
    kb.row(InlineKeyboardButton(text="🧪 Отправить тест", callback_data="kindle_test"))
    kb.row(
        InlineKeyboardButton(text="🕘 История", callback_data="kindle_history_home"),
        InlineKeyboardButton(text="❓ Инструкция", callback_data="kindle_setup_help"),
    )
    kb.row(InlineKeyboardButton(text="🗑 Удалить Kindle e-mail", callback_data="kindle_remove_confirm"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()


def _kindle_email_input_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Отмена", callback_data="kindle_email_cancel"))
    return kb.as_markup()


def _kindle_setup_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📮 Сохранить Kindle e-mail", callback_data="kindle_email_edit"))
    kb.row(InlineKeyboardButton(text="📨 Показать отправителя", callback_data="kindle_sender"))
    kb.row(InlineKeyboardButton(text="⚙️ Kindle меню", callback_data="kindle_home"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()


def _kindle_format_keyboard(current: str):
    kb = InlineKeyboardBuilder()
    for fmt in ("epub", "fb2", "txt", "pdf"):
        label = f"{'✅ ' if fmt == current else ''}{fmt.upper()}"
        kb.button(text=label, callback_data=f"kindle_fmt:{fmt}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="⚙️ Kindle меню", callback_data="kindle_home"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()
