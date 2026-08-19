from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.repositories.web_access import WebAccessRepository


def build_web_router(
    *,
    web_access_repo: WebAccessRepository | None,
    public_url: str,
    pair_code_ttl_seconds: int,
) -> Router:
    router = Router()

    async def send_access(message: Message, user_id: int) -> None:
        if web_access_repo is None:
            await message.answer("Веб-библиотека пока не включена.")
            return
        code = await web_access_repo.create_pairing_code(user_id, pair_code_ttl_seconds)
        sessions = await web_access_repo.list_sessions(user_id)
        minutes = max(1, pair_code_ttl_seconds // 60)
        await message.answer(
            "<b>Полка в браузере</b>\n\n"
            f"1. Открой на читалке:\n<a href=\"{escape(public_url, quote=True)}\">{escape(public_url)}</a>\n\n"
            f"2. Введи код: <code>{escape(code)}</code>\n\n"
            f"Код одноразовый и действует {minutes} минут.\n"
            f"Подключено браузеров: {len(sessions)}.",
            reply_markup=_web_keyboard(public_url, bool(sessions)),
        )

    @router.message(Command("web"))
    async def web_command(message: Message) -> None:
        await send_access(message, message.from_user.id)

    @router.callback_query(F.data == "web_access")
    async def web_access(callback: CallbackQuery) -> None:
        await callback.answer()
        await send_access(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "web_access_refresh")
    async def web_access_refresh(callback: CallbackQuery) -> None:
        await callback.answer("Создал новый код")
        await send_access(callback.message, callback.from_user.id)

    @router.callback_query(F.data == "web_sessions_revoke_all")
    async def revoke_all(callback: CallbackQuery) -> None:
        if web_access_repo is None:
            return
        count = await web_access_repo.revoke_all_sessions(callback.from_user.id)
        await callback.answer(f"Отключено: {count}")
        await callback.message.answer("Все браузеры отключены. Для нового входа создай одноразовый код.")

    return router


def _web_keyboard(public_url: str, has_sessions: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Открыть веб-библиотеку", url=public_url))
    kb.row(InlineKeyboardButton(text="Новый код", callback_data="web_access_refresh"))
    if has_sessions:
        kb.row(InlineKeyboardButton(text="Отключить все браузеры", callback_data="web_sessions_revoke_all"))
    kb.row(InlineKeyboardButton(text="← Мои устройства", callback_data="readers_home"))
    return kb.as_markup()
