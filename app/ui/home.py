from __future__ import annotations

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def home_text() -> str:
    return (
        "<b>Библиотека им. Недзвецких</b>\n\n"
        "Напиши название, автора или просто опиши, что хочется почитать.\n\n"
        "<b>Примеры:</b>\n"
        "• Дюна\n"
        "• Пелевин\n"
        "• исповедь толстой\n"
        "• антиутопия\n"
        "• что-то как 1984, но современнее"
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
        InlineKeyboardButton(text="⚙️ Kindle", callback_data="kindle_home"),
    )
    kb.row(InlineKeyboardButton(text="❓ Помощь", callback_data="home_help"))
    return kb.as_markup()


def help_text() -> str:
    return (
        "<b>Как пользоваться</b>\n\n"
        "Просто напиши, что ищешь:\n"
        "• название книги\n"
        "• автора\n"
        "• автора + название\n"
        "• жанр, настроение или идею\n\n"
        "<b>Примеры:</b>\n"
        "• мастер и маргарита\n"
        "• эдит патту\n"
        "• исповедь толстой\n"
        "• подборка русского постмодерна как Пелевин\n\n"
        "В карточке книги можно:\n"
        "• скачать файл\n"
        "• отправить на Kindle\n"
        "• добавить в избранное"
    )


def help_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⚙️ Kindle", callback_data="kindle_home"))
    kb.row(InlineKeyboardButton(text="⭐ Избранное", callback_data="home_favorites"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()


def search_help_text() -> str:
    return (
        "<b>Как искать</b>\n\n"
        "Пиши обычным языком. Я сам попробую понять, что это: книга, автор или подборка.\n\n"
        "<b>Хорошие запросы:</b>\n"
        "• Дюна\n"
        "• Лев Толстой\n"
        "• исповедь толстой\n"
        "• книги как 1984\n"
        "• мрачное фэнтези без подростковости\n\n"
        "Если не нашлось — попробуй короче: обычно лучше работает название без лишних слов."
    )


def back_home_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()
