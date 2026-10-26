import asyncio

import httpx

from app.flibusta import FlibustaClient


def test_browse_uses_source_order_genres_and_excludes_sidebar():
    async def scenario():
        calls = []

        def respond(request):
            calls.append(str(request.url))
            if request.url.path == "/g":
                body = '<div id="main"><a href="/g/detective">Детективы</a><a href="/g/../bad">Bad</a></div>'
            elif request.url.path == "/rec":
                assert request.url.params["srcgenre"] == "detective"
                body = '<div id="main"><form name="formrecs"><table><tr><td><a href="/a/9">Автор</a><a href="/b/42">Книга</a></td></tr></table></form></div>'
            else:
                assert request.url.path == "/stat/w"
                body = '<div id="main"><ul><li><a href="/a/7">Оруэлл</a><a href="/b/640151">1984</a></li><li><a href="/b/2">Вторая</a></li></ul></div>'
            return httpx.Response(
                200, text=body + '<aside><a href="/b/999">Посторонняя</a></aside>'
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = FlibustaClient("https://catalog.test", client=http)
            assert [b.book_id for b in await client.popular_books()] == ["640151", "2"]
            assert await client.genres() == [{"code": "detective", "name": "Детективы"}]
            assert [b.book_id for b in await client.genre_books("detective")] == ["42"]
            try:
                await client.genre_books("../secret")
                assert False, "invalid genre accepted"
            except ValueError:
                pass
            assert len(calls) == 3

    asyncio.run(scenario())


def test_missing_browse_container_never_parses_sidebar():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, text='<aside><a href="/b/999">Wrong</a></aside>'
                )
            )
        ) as http:
            client = FlibustaClient("https://catalog.test", client=http)
            assert await client.popular_books() == []
            assert await client.genre_books("detective") == []
            assert await client.genres() == []

    asyncio.run(scenario())


def test_genre_selection_validates_against_actual_catalog_and_restores_context():
    from app.services.catalog_browse import browse_catalog
    from app.services.search.context import search_status

    class Catalog:
        async def genres(self):
            return [{"code": "detective", "name": "Детективы"}]

        async def genre_books(self, code):
            assert code == "detective"
            return []

    async def scenario():
        result = await browse_catalog(Catalog(), "detective")
        assert "Детективы" in result[0]
        assert search_status.get() is None
        try:
            await browse_catalog(Catalog(), "not-a-real-genre")
            assert False
        except ValueError:
            pass
        assert search_status.get() is None

    asyncio.run(scenario())


def test_source_genre_headings_are_preserved():
    async def scenario():
        html='<div id="main"><h3>Фантастика</h3><ul><li><a href="/g/sf">Научная фантастика</a></li></ul><h3>Проза</h3><a href="/g/prose">Проза</a></div>'
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=html))) as http:
            rows=await FlibustaClient('https://catalog.test',client=http).genres()
            assert [g['group'] for g in rows]==['Фантастика','Проза']
    asyncio.run(scenario())
