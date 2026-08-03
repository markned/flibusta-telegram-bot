from __future__ import annotations

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def home_text() -> str:
    return (
        "<b>Библиотека им. Недзвецких</b>\n\n"
        "Напиши название книги, автора или название и автора вместе.\n\n"
        "<b>Примеры:</b>\n"
        "• Дюна\n"
        "• Пелевин\n"
        "• исповедь толстой\n"
        "• мастер и маргарита"
    )


def home_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔎 Как искать", callback_data="home_search_help"))
    kb.row(
        InlineKeyboardButton(text="⭐ Избранное", callback_data="home_favorites"),
        InlineKeyboardButton(text="🕘 История", callback_data="home_history"),
    )
    kb.row(
        InlineKeyboardButton(text="📚 Последняя книга", callback_data="home_last"),
        InlineKeyboardButton(text="📱 Читалки", callback_data="readers_home"),
    )
    kb.row(InlineKeyboardButton(text="❓ Помощь", callback_data="home_help"))
    return kb.as_markup()


def help_text() -> str:
    return (
        "<b>Как пользоваться</b>\n\n"
        "Просто напиши, что ищешь:\n"
        "• название книги\n"
        "• автора\n"
        "• автора + название\n\n"
        "<b>Примеры:</b>\n"
        "• мастер и маргарита\n"
        "• эдит патту\n"
        "• исповедь толстой\n"
        "• Достоевский — Идиот\n\n"
        "В карточке книги можно:\n"
        "• скачать файл\n"
        "• отправить на Kindle или PocketBook\n"
        "• добавить в избранное"
    )


def help_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📱 Читалки", callback_data="readers_home"))
    kb.row(InlineKeyboardButton(text="⭐ Избранное", callback_data="home_favorites"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()


def search_help_text() -> str:
    return (
        "<b>Как искать</b>\n\n"
        "Пиши название, автора или оба сразу — регистр не важен.\n\n"
        "<b>Хорошие запросы:</b>\n"
        "• Дюна\n"
        "• Лев Толстой\n"
        "• исповедь толстой\n"
        "• Толстой — Война и мир\n"
        "• l.yf (если случайно включена английская раскладка)\n\n"
        "Если не нашлось — попробуй короче: обычно лучше работает название без лишних слов."
    )


def back_home_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()
