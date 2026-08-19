from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.repositories.reader_settings import ReaderSettings, ReaderSettingsRepository
from app.services.email_sender import EmailSender
from app.services.kindle import KindleEmailInvalidError, mask_email, validate_pocketbook_email
from app.services.kindle_queue import KindleQueue

logger = logging.getLogger(__name__)
POCKETBOOK_FORMATS = ("epub", "fb2", "pdf", "txt")


class PocketBookEmailForm(StatesGroup):
    waiting_for_email = State()


def build_readers_router(
    *,
    kindle_settings_repo: KindleSettingsRepository,
    reader_settings_repo: ReaderSettingsRepository,
    deliveries_repo: KindleDeliveriesRepository,
    delivery_queue: KindleQueue,
    email_sender: EmailSender,
    smtp_from_email: str | None,
    smtp_config_present: bool,
    web_enabled: bool = False,
) -> Router:
    router = Router()

    async def send_readers_home(message: Message, user_id: int) -> None:
        kindle = await kindle_settings_repo.get(user_id)
        pocketbook = await reader_settings_repo.get(user_id, "pocketbook")
        lines = ["<b>Мои устройства</b>", ""]
        lines.append(f"Kindle: {'✅ настроен' if kindle else 'не настроен'}")
        lines.append(f"PocketBook: {'✅ настроен' if pocketbook else 'не настроен'}")
        lines.extend(("", "Настрой устройство один раз, затем отправляй книги прямо из карточки."))
        await message.answer("\n".join(lines), reply_markup=_readers_keyboard(web_enabled))

    async def send_pocketbook_home(message: Message, user_id: int) -> None:
        settings = await reader_settings_repo.get(user_id, "pocketbook")
        await message.answer(
            _pocketbook_home_text(settings, smtp_from_email),
            reply_markup=_pocketbook_home_keyboard(settings),
        )

    @router.message(Command("readers"))
    @router.message(F.text.in_({"📱 Мои устройства", "📱 Читалки"}))
    async def readers_home_message(message: Message) -> None:
        await send_readers_home(message, message.from_user.id)

    @router.message(Command("pocketbook"))
    async def pocketbook_message(message: Message) -> None:
        await send_pocketbook_home(message, message.from_user.id)

    @router.callback_query(F.data == "readers_home")
    async def readers_home_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await send_readers_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "pocketbook_home")
    async def pocketbook_home_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await send_pocketbook_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "pocketbook_email_edit")
    async def pocketbook_email_edit(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(PocketBookEmailForm.waiting_for_email)
        await callback.message.answer(
            "Пришли адрес Send-to-PocketBook одним сообщением.\n"
            "Он выглядит так: <code>username@pbsync.com</code>.",
            reply_markup=_cancel_keyboard(),
        )

    @router.message(StateFilter(PocketBookEmailForm.waiting_for_email))
    async def pocketbook_email_input(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        if raw.lower() in {"отмена", "cancel", "/cancel"}:
            await state.clear()
            await message.answer("Ок, отменил.")
            return
        try:
            email = validate_pocketbook_email(raw)
        except KindleEmailInvalidError as exc:
            await message.answer(escape(str(exc)), reply_markup=_cancel_keyboard())
            return
        current = await reader_settings_repo.get(message.from_user.id, "pocketbook")
        preferred = current.preferred_format if current else "epub"
        await reader_settings_repo.upsert(
            message.from_user.id,
            "pocketbook",
            email,
            preferred_format=preferred,
        )
        await state.clear()
        await message.answer(f"Готово, сохранил: {mask_email(email)}")
        await send_pocketbook_home(message, message.from_user.id)

    @router.callback_query(F.data == "pocketbook_email_cancel")
    async def pocketbook_email_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer("Ок, отменил.")
        await send_pocketbook_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "pocketbook_sender")
    async def pocketbook_sender(callback: CallbackQuery) -> None:
        await callback.answer()
        if not smtp_from_email:
            await callback.message.answer("Отправка на читалки пока не настроена владельцем бота.")
            return
        await callback.message.answer(
            "Отправитель бота:\n"
            f"<code>{escape(smtp_from_email)}</code>\n\n"
            "После первого письма PocketBook предложит добавить этот адрес в список доверенных отправителей."
        )

    @router.callback_query(F.data == "pocketbook_sender_confirmed")
    async def pocketbook_sender_confirmed(callback: CallbackQuery) -> None:
        settings = await reader_settings_repo.set_sender_confirmed(callback.from_user.id, "pocketbook", True)
        await callback.answer("Запомнил")
        if settings is None:
            await send_pocketbook_home(callback.message, callback.from_user.id)
            return
        await callback.message.answer("Отлично. Отправитель отмечен как доверенный.")
        await send_pocketbook_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "pocketbook_format")
    async def pocketbook_format(callback: CallbackQuery) -> None:
        await callback.answer()
        settings = await reader_settings_repo.get(callback.from_user.id, "pocketbook")
        if settings is None:
            await send_pocketbook_home(callback.message, callback.from_user.id)
            return
        await callback.message.answer(
            f"Выбери формат для PocketBook. Сейчас: <b>{settings.preferred_format.upper()}</b>",
            reply_markup=_pocketbook_format_keyboard(settings.preferred_format),
        )

    @router.callback_query(F.data.startswith("pocketbook_fmt:"))
    async def pocketbook_format_set(callback: CallbackQuery) -> None:
        value = callback.data.split(":", 1)[1]
        if value not in POCKETBOOK_FORMATS:
            await callback.answer("Неизвестный формат")
            return
        await reader_settings_repo.update_format(callback.from_user.id, "pocketbook", value)
        await callback.answer("Сохранено")
        await send_pocketbook_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "pocketbook_history")
    async def pocketbook_history(callback: CallbackQuery) -> None:
        await callback.answer()
        items = await deliveries_repo.get_recent_for_user(callback.from_user.id, limit=10, provider="pocketbook")
        if not items:
            await callback.message.answer("Отправок на PocketBook пока не было.")
            return
        lines = ["<b>Последние отправки на PocketBook</b>"]
        for item in items:
            lines.append(f"{item.created_at[:16]} — {escape(item.title or item.book_id)} [{(item.format or '?').upper()}] — {item.status}")
        await callback.message.answer("\n".join(lines))

    @router.callback_query(F.data == "pocketbook_test")
    async def pocketbook_test(callback: CallbackQuery) -> None:
        settings = await reader_settings_repo.get(callback.from_user.id, "pocketbook")
        if settings is None:
            await callback.answer()
            await send_pocketbook_home(callback.message, callback.from_user.id)
            return
        if not smtp_config_present:
            await callback.answer()
            await callback.message.answer("Отправка на читалки пока не настроена владельцем бота.")
            return
        await callback.answer("Отправляю тест")
        status = await callback.message.answer("Отправляю тестовое письмо на PocketBook…")
        try:
            await email_sender.send_attachment(
                to_email=settings.destination_email,
                subject="PocketBook test",
                filename="pocketbook-test.txt",
                content=b"PocketBook delivery is configured.",
                content_type="text/plain",
            )
        except Exception as exc:
            logger.error("PocketBook test failed error_type=%s", type(exc).__name__)
            await status.edit_text("Не удалось отправить тест. Проверь настройки и попробуй позже.")
            return
        await status.edit_text("Тест отправлен. Подтверди отправителя в PocketBook, если устройство попросит.")

    @router.callback_query(F.data == "pocketbook_remove")
    async def pocketbook_remove(callback: CallbackQuery) -> None:
        await reader_settings_repo.delete(callback.from_user.id, "pocketbook")
        await callback.answer("Удалено")
        await send_pocketbook_home(callback.message, callback.from_user.id)

    @router.callback_query(F.data.startswith("reader_send:"))
    async def reader_send(callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        if len(parts) == 2:
            book_id = parts[1]
            kindle = await kindle_settings_repo.get(callback.from_user.id)
            pocketbook = await reader_settings_repo.get(callback.from_user.id, "pocketbook")
            configured = [name for name, value in (("kindle", kindle), ("pocketbook", pocketbook)) if value]
            if not configured:
                await callback.answer()
                await callback.message.answer(
                    "Сначала настрой читалку — это займёт минуту.",
                    reply_markup=_readers_keyboard(web_enabled),
                )
                return
            if len(configured) > 1:
                await callback.answer()
                await callback.message.answer("Куда отправить книгу?", reply_markup=_reader_choice_keyboard(book_id))
                return
            provider = configured[0]
        elif len(parts) == 3 and parts[1] in {"kindle", "pocketbook"}:
            provider, book_id = parts[1], parts[2]
        else:
            await callback.answer("Не удалось определить читалку")
            return
        await _enqueue(callback, provider, book_id)

    async def _enqueue(callback: CallbackQuery, provider: str, book_id: str) -> None:
        label = "PocketBook" if provider == "pocketbook" else "Kindle"
        if provider == "kindle" and await kindle_settings_repo.get(callback.from_user.id) is None:
            await callback.answer()
            await callback.message.answer("Kindle ещё не настроен.", reply_markup=_readers_keyboard(web_enabled))
            return
        if provider == "pocketbook" and await reader_settings_repo.get(callback.from_user.id, provider) is None:
            await callback.answer()
            await send_pocketbook_home(callback.message, callback.from_user.id)
            return
        await callback.answer(f"Добавил в очередь {label}")
        status = await callback.message.answer(f"Добавил в очередь {label}…")
        try:
            await delivery_queue.enqueue(
                user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                book_id=book_id,
                status_message_id=status.message_id,
                provider=provider,
            )
        except Exception as exc:
            logger.error("Reader enqueue failed provider=%s error_type=%s", provider, type(exc).__name__)
            await status.edit_text(f"Не удалось добавить отправку на {label}. Попробуй позже.")

    return router


def _pocketbook_home_text(settings: ReaderSettings | None, smtp_from_email: str | None) -> str:
    if settings is None:
        return (
            "<b>PocketBook</b>\n"
            "Статус: ❌ не настроен\n\n"
            "Открой на устройстве Send-to-PocketBook и сохрани здесь выданный адрес <code>@pbsync.com</code>."
        )
    warning = "" if settings.sender_confirmed else "\n\n⚠️ Подтверди отправителя бота в PocketBook после первого письма."
    return (
        "<b>PocketBook</b>\n"
        "Статус: ✅ настроен\n\n"
        f"Адрес: {escape(mask_email(settings.destination_email))}\n"
        f"Формат: {settings.preferred_format.upper()}\n"
        f"Отправитель: {escape(mask_email(smtp_from_email))}"
        f"{warning}"
    )


def _readers_keyboard(web_enabled: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Kindle", callback_data="kindle_home"))
    kb.row(InlineKeyboardButton(text="PocketBook", callback_data="pocketbook_home"))
    if web_enabled:
        kb.row(InlineKeyboardButton(text="🌐 Веб-библиотека", callback_data="web_access"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()


def _pocketbook_home_keyboard(settings: ReaderSettings | None):
    kb = InlineKeyboardBuilder()
    if settings is None:
        kb.row(InlineKeyboardButton(text="📮 Сохранить адрес PocketBook", callback_data="pocketbook_email_edit"))
        kb.row(InlineKeyboardButton(text="📨 Показать отправителя", callback_data="pocketbook_sender"))
    else:
        kb.row(InlineKeyboardButton(text="📮 Изменить адрес", callback_data="pocketbook_email_edit"))
        kb.row(InlineKeyboardButton(text=f"📄 Формат: {settings.preferred_format.upper()}", callback_data="pocketbook_format"))
        kb.row(InlineKeyboardButton(text="✅ Отправитель подтверждён", callback_data="pocketbook_sender_confirmed"))
        kb.row(InlineKeyboardButton(text="🧪 Тест", callback_data="pocketbook_test"))
        kb.row(InlineKeyboardButton(text="🕘 История", callback_data="pocketbook_history"))
        kb.row(InlineKeyboardButton(text="🗑 Удалить адрес", callback_data="pocketbook_remove"))
    kb.row(InlineKeyboardButton(text="← Мои устройства", callback_data="readers_home"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()


def _pocketbook_format_keyboard(current: str):
    kb = InlineKeyboardBuilder()
    for fmt in POCKETBOOK_FORMATS:
        kb.button(text=f"{'✅ ' if fmt == current else ''}{fmt.upper()}", callback_data=f"pocketbook_fmt:{fmt}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="← PocketBook", callback_data="pocketbook_home"))
    return kb.as_markup()


def _reader_choice_keyboard(book_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Kindle", callback_data=f"reader_send:kindle:{book_id}"))
    kb.row(InlineKeyboardButton(text="PocketBook", callback_data=f"reader_send:pocketbook:{book_id}"))
    return kb.as_markup()


def _cancel_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Отмена", callback_data="pocketbook_email_cancel"))
    return kb.as_markup()
