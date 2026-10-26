"""Bounded, source-backed browsing shared by Telegram and the e-ink web UI."""

from app.services.search.context import SearchStatus, search_status


async def browse_catalog(client, genre=None, *, genres_only=False):
    status = SearchStatus()
    token = search_status.set(status)
    try:
        if genres_only or genre:
            genres = await client.genres()
            if genres_only:
                return "Жанры Flibusta", [], genres, status
            selected = next((item for item in genres if item["code"] == genre), None)
            if selected is None:
                raise ValueError("Unknown genre")
            books = await client.genre_books(genre)
            return f"Рекомендации сообщества: {selected['name']}", books, [], status
        return (
            "Топ Flibusta за последние 7 дней по скачиваниям",
            await client.popular_books(),
            [],
            status,
        )
    finally:
        search_status.reset(token)
