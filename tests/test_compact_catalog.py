from app.ui.library import main_reply_keyboard, search_results_keyboard
from app.ui.catalog import catalog_menu, genre_menu
from app.state import create_search_session


def test_compact_persistent_menu_and_catalog_actions():
    assert [b.text for row in main_reply_keyboard().keyboard for b in row] == [
        "📚 Моя полка",
        "🔎 Каталог",
    ]
    buttons = [b for row in catalog_menu().inline_keyboard for b in row]
    assert {b.callback_data for b in buttons} >= {
        "browse:tops",
        "browse:genres",
        "browse:random",
    }


def test_genres_group_then_sort_and_page_eight():
    genres = [
        {"code": f"f{i}", "name": name, "group": "Фантастика"}
        for i, name in enumerate(["Я", "А", "Е", "Д", "Г", "В", "Б", "Ж", "З"])
    ]
    text, keyboard = genre_menu(genres)
    assert "Жанры" in text
    callback = next(
        b.callback_data
        for row in keyboard.inline_keyboard
        for b in row
        if b.text == "Фантастика и фэнтези"
    )
    text, keyboard = genre_menu(genres, callback.removeprefix("browse:"))
    names = [row[0].text for row in keyboard.inline_keyboard[:8]]
    assert names == ["А", "Б", "В", "Г", "Д", "Е", "Ж", "З"]
    assert any(b.text == "Дальше →" for row in keyboard.inline_keyboard for b in row)


def test_collection_books_keep_context_in_callback():
    from app.flibusta import SearchResult

    session = create_search_session(
        1,
        2,
        "Топ",
        [SearchResult("42", "Книга", "Автор")],
        title="Топ",
        return_to="browse:tops",
    )
    markup = search_results_keyboard(session)
    assert (
        markup.inline_keyboard[0][0].callback_data == f"cbook:{session.session_id}:42"
    )
    assert markup.inline_keyboard[-1][0].callback_data == "browse:tops"
