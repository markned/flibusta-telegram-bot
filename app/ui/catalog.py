"""Small hierarchical Telegram catalog menus; source genres stay discoverable."""

from hashlib import sha256
from html import escape

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.services.popular_collections import SOURCES


def _markup(items):
    kb = InlineKeyboardBuilder()
    for text, callback in items:
        kb.row(InlineKeyboardButton(text=text[:64], callback_data=callback))
    return kb.as_markup()


def catalog_menu():
    return _markup(
        [
            ("🔥 Популярное", "browse:tops"),
            ("📖 Жанры", "browse:genres"),
            ("🎲 Случайная книга", "browse:random"),
        ]
    )


def shelf_menu():
    return _markup(
        [
            ("⭐ Избранное", "home_favorites"),
            ("🕘 История", "home_history"),
            ("📚 Последняя", "home_last"),
            ("📱 Мои устройства", "readers_home"),
            ("❓ Помощь", "home_help"),
        ]
    )


def tops_menu():
    return _markup(
        [(label, "browse:top:" + key) for key, label in SOURCES.items()]
        + [("← Каталог", "browse:catalog")]
    )


def group_name(genre):
    source = genre.get("group") or "Прочее"
    if source.startswith("Детск"):
        return "Детское"
    return {
        "Фантастика": "Фантастика и фэнтези",
        "Детективы и триллеры": "Детективы",
        "Документальная литература": "Нон-фикшн",
        "Наука, Образование": "Нон-фикшн",
        "Деловая литература": "Нон-фикшн",
        "Справочная литература": "Нон-фикшн",
        "Учебники и пособия": "Нон-фикшн",
    }.get(source, source)


def group_key(name):
    return sha256(name.encode()).hexdigest()[:8]


def genre_groups(genres):
    groups = {}
    for genre in genres:
        name = group_name(genre)
        groups.setdefault(group_key(name), {"name": name, "genres": []})[
            "genres"
        ].append(genre)
    for group in groups.values():
        group["genres"].sort(key=lambda g: g["name"].casefold().replace("ё", "е"))
    return groups


def genre_menu(genres, action="genres"):
    groups = genre_groups(genres)
    names = sorted(groups, key=lambda k: groups[k]["name"].casefold())
    if action == "genres":
        preferred = [
            "Фантастика и фэнтези",
            "Детективы",
            "Любовные романы",
            "Нон-фикшн",
            "Детское",
        ]
        items = [
            (name, "browse:group:" + group_key(name) + ":0")
            for name in preferred
            if group_key(name) in groups
        ]
        items += [("Все разделы", "browse:groups:0"), ("← Каталог", "browse:catalog")]
        return "<b>Жанры</b>", _markup(items)
    parts = action.split(":")
    if parts[0] == "groups":
        page = max(0, int(parts[1]))
        values = [(groups[k]["name"], "browse:group:" + k + ":0") for k in names]
        title, prefix, back = "Все разделы", "browse:groups:", "browse:genres"
    elif parts[0] == "group" and parts[1] in groups:
        group = groups[parts[1]]
        page = max(0, int(parts[2]))
        values = [(g["name"], "browse:genre:" + g["code"]) for g in group["genres"]]
        title, prefix, back = (
            group["name"],
            f"browse:group:{parts[1]}:",
            "browse:genres",
        )
    else:
        raise ValueError("Unknown genre group")
    pages = max(1, (len(values) + 7) // 8)
    page = min(page, pages - 1)
    kb = InlineKeyboardBuilder.from_markup(_markup(values[page * 8 : (page + 1) * 8]))
    if pages > 1:
        nav = []
        if page:
            nav.append(
                InlineKeyboardButton(
                    text="← Назад", callback_data=prefix + str(page - 1)
                )
            )
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop")
        )
        if page + 1 < pages:
            nav.append(
                InlineKeyboardButton(
                    text="Дальше →", callback_data=prefix + str(page + 1)
                )
            )
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="← Разделы", callback_data=back))
    return f"<b>{escape(title)}</b>", kb.as_markup()
