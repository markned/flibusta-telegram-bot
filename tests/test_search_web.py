"""Offline search-to-book flow through the authenticated web interface."""
import asyncio
from contextlib import asynccontextmanager

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from bs4 import BeautifulSoup

from app.flibusta import AuthorResult, FlibustaError, SearchResult
from app.repositories.access import AccessRepository
from app.repositories.db import Database
from app.repositories.download_history import DownloadHistoryRepository
from app.repositories.favorites import FavoritesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.repositories.last_books import LastBooksRepository
from app.repositories.reader_settings import ReaderSettingsRepository
from app.repositories.web_access import WebAccessRepository
from app.services.search.types import SearchMode, SearchOutcome, SearchPlan
from app.web.app import WebDependencies, build_web_app


@asynccontextmanager
async def browser(tmp_path, service, catalog=None):
    db = Database(str(tmp_path / "search.sqlite"))
    await db.initialize()
    access = AccessRepository(db)
    await access.ensure_user(42, "approved")
    auth = WebAccessRepository(db, "test-secret")
    deps = WebDependencies(
        search_service=service, flibusta=catalog or object(), web_access_repo=auth,
        access_repo=access, favorites_repo=FavoritesRepository(db),
        history_repo=DownloadHistoryRepository(db), last_books_repo=LastBooksRepository(db),
        kindle_settings_repo=KindleSettingsRepository(db), reader_settings_repo=ReaderSettingsRepository(db),
        delivery_queue=object(), admin_ids=set(), access_control_enabled=True,
        session_days=2, max_sessions_per_user=2, download_max_bytes=1024,
        download_concurrency=1, search_rate_limit_per_minute=20,
        download_rate_limit_per_hour=5, cookie_secure=False,
    )
    client = TestClient(TestServer(build_web_app(deps)), cookie_jar=CookieJar(unsafe=True))
    await client.start_server()
    try:
        code = await auth.create_pairing_code(42)
        await client.post("/pair", data={"code": code})
        yield client
    finally:
        await client.close()


def test_web_expansion_and_pagination_preserve_query_and_show_all_results(tmp_path):
    class Service:
        def __init__(self):
            self.expansions = []

        async def search(self, query, *, expanded=False):
            self.expansions.append(expanded)
            return SearchOutcome(SearchPlan(query, query, SearchMode.EXACT, (query,), ()),
                                 [SearchResult(str(n), f"Дюна {n}", "Фрэнк Герберт") for n in range(25)],
                                 [AuthorResult(str(n), f"Автор {n}") for n in range(15)], (query,),
                                 has_more=not expanded)

    async def scenario():
        service = Service()
        async with browser(tmp_path, service) as client:
            first = BeautifulSoup(await (await client.get("/search", params={"q": 'Дюна & "<книга>"'})).text(), "html.parser")
            more = first.find("a", string="Искать дополнительные варианты")
            assert more is not None
            next_page = first.find("a", string="Дальше →")
            assert next_page is not None
            second = BeautifulSoup(await (await client.get(next_page["href"])).text(), "html.parser")
            assert second.find("a", href="/book/24") is not None
            assert second.find("a", href="/author/14") is not None
            await client.get(more["href"])
            assert service.expansions == [False, False, True]
            assert first.find("книга") is None
    asyncio.run(scenario())


def test_web_failure_keeps_query_and_does_not_claim_book_absent(tmp_path):
    class Service:
        async def search(self, query):
            raise FlibustaError("private upstream details")

    async def scenario():
        async with browser(tmp_path, Service()) as client:
            response = await client.get("/search", params={"q": "Дюна"})
            text = await response.text()
            assert response.status == 503
            assert "Источник временно недоступен" in text
            assert "Ничего не нашлось" not in text and "private upstream" not in text
            assert "Повторить поиск" in text and "Дюна" in text
    asyncio.run(scenario())


def test_web_labels_stale_cache_and_separates_approximate_results(tmp_path):
    class Service:
        async def search(self, query):
            return SearchOutcome(SearchPlan(query, query, SearchMode.EXACT, (query,), ()),
                                 [SearchResult("10", "Дюна", "Фрэнк Герберт"),
                                  SearchResult("11", "Дюны", "Автор")], [], (query,),
                                 source_unavailable=True, stale_at="2026-01-02T03:04:00+00:00")

    async def scenario():
        async with browser(tmp_path, Service()) as client:
            response = await client.get("/search", params={"q": "Дюна"}, headers={"User-Agent": "Kindle/5.0"})
            text = await response.text()
            assert "Результаты из кэша" in text and "02.01.2026" in text
            assert "Приблизительные совпадения" in text
            assert "Повторить поиск" in text
            assert "<script" not in text and "display: flex" not in text
    asyncio.run(scenario())


def test_web_author_search_shows_works_not_biographies_and_offers_explicit_about(tmp_path):
    from app.services.search import SearchService

    class Catalog:
        async def search_authors(self, query, limit):
            return [AuthorResult("1", "Лев Толстой"), AuthorResult("2", "Алексей Толстой")]

        async def author_books(self, author_id, limit):
            return "Лев Толстой", [SearchResult("200", "Война и мир", "Лев Толстой")]

        async def search(self, query, limit):
            return [SearchResult("100", "Лев Толстой. Биография", "Павел Басинский"),
                    SearchResult("200", "Война и мир", "Лев Толстой")]

    async def scenario():
        service = SearchService(Catalog(), book_limit=40, author_limit=20)
        async with browser(tmp_path, service) as client:
            html = BeautifulSoup(await (await client.get("/search", params={"q": "лев толстой"})).text(), "html.parser")
            assert "Книги автора" in html.get_text()
            assert html.find("a", href="/book/200") is not None
            assert html.find("a", href="/book/100") is None
            assert "Приблизительные совпадения" not in html.get_text()
            about = html.find("a", string="Книги об авторе")
            assert about is not None
            about_html = BeautifulSoup(await (await client.get(about["href"])).text(), "html.parser")
            assert about_html.find("a", href="/book/100") is not None
            assert about_html.find("a", href="/book/200") is None
            ambiguous = BeautifulSoup(await (await client.get("/search", params={"q": "Толстой"})).text(), "html.parser")
            assert "Уточни автора" in ambiguous.get_text()
            assert ambiguous.find("a", href="/author/1") and ambiguous.find("a", href="/author/2")
    asyncio.run(scenario())


def test_web_author_typo_is_a_confirmation_link_not_silent_replacement(tmp_path):
    from app.services.search import SearchService

    class Catalog:
        async def search_authors(self, query, limit):
            return []
    async def scenario():
        async with browser(tmp_path, SearchService(Catalog(), book_limit=40, author_limit=20)) as client:
            html = BeautifulSoup(await (await client.get("/search", params={"q": "лев толтсой"})).text(), "html.parser")
            assert "Вы имели в виду" in html.get_text()
            assert html.find("input", attrs={"name": "q"})["value"] == "лев толтсой"
            assert html.find("a", string="Лев Толстой") is not None
    asyncio.run(scenario())


def test_about_author_retry_keeps_explicit_intent(tmp_path):
    class Service:
        async def search_about_author(self, query):
            raise FlibustaError("offline")

    async def scenario():
        async with browser(tmp_path, Service()) as client:
            response = await client.get("/search", params={"q": "Лев Толстой", "mode": "about"})
            html = BeautifulSoup(await response.text(), "html.parser")
            retry = html.find("a", string="Повторить поиск")
            assert response.status == 503
            assert "mode=about" in retry["href"]
    asyncio.run(scenario())


def test_web_query_clarification_escapes_and_preserves_choices():
    from app.web.ui import search_page
    markup = search_page('эко', [], [], query_choices=('книги Умберто Эко', '«<эко>»'), clarification='Что искать?')
    soup = BeautifulSoup(markup, 'html.parser')
    assert 'Что искать?' in soup.get_text()
    assert 'Ничего не нашлось' not in soup.get_text()
    assert soup.find('эко') is None
    assert soup.find('a', string='книги Умберто Эко')['href'].startswith('/search?')


def test_authenticated_browsing_paginates_and_validates_genres(tmp_path):
    class Catalog:
        async def popular_books(self):
            return [SearchResult(str(i), f'Book {i}', 'Author') for i in range(42)]
        async def genres(self):
            return [{'code': 'detective', 'name': 'Детективы', 'group':'Детективы и триллеры'}]
        async def genre_books(self, code):
            assert code == 'detective'
            return [SearchResult('50', 'Детектив', 'Автор')]
    async def scenario():
        async with browser(tmp_path, object(), Catalog()) as client:
            root = await (await client.get('/browse')).text()
            assert 'Яндекс Книги' in root and 'Литрес' in root
            response = await client.get('/browse?source=flibusta')
            soup = BeautifulSoup(await response.text(), 'html.parser')
            assert response.status == 200
            assert 'Flibusta' in soup.get_text()
            assert soup.find('a', href=lambda h:h and h.startswith('/book/7?'))
            assert not soup.find('a', href=lambda h:h and h.startswith('/book/8?'))
            next_page = soup.find('a', string='Дальше →')
            second = await (await client.get(next_page['href'])).text()
            assert '/book/8?' in second
            genres = await (await client.get('/browse?view=genres')).text()
            assert 'group=' in genres and 'Детективы' in genres
            result = await client.get('/browse?genre=detective')
            assert '/book/50' in await result.text()
            assert (await client.get('/browse?genre=missing')).status == 400
            client.session.cookie_jar.clear()
            unauthenticated = await client.get('/browse', allow_redirects=False)
            assert unauthenticated.status == 303
    asyncio.run(scenario())


def test_collection_return_url_preserves_page_and_cannot_redirect_offsite():
    from types import SimpleNamespace
    from app.web.app import _collection_return
    assert _collection_return(SimpleNamespace(query={'collection':'litres','collection_page':'3'})) == '/browse?page=3&source=litres'
    assert _collection_return(SimpleNamespace(query={'collection':'https://evil.test'})) is None
    assert _collection_return(SimpleNamespace(query={'collection':'genre','genre':'detective','collection_page':'1'})) == '/browse?page=1&genre=detective'


def test_web_work_default_edition_selection_and_owner_isolation(tmp_path):
    from app.flibusta import BookDetails
    class Service:
        async def search(self,query):
            return SearchOutcome(SearchPlan(query,query,SearchMode.EXACT,(query,),()),
                [SearchResult('1','Книга','Автор'),SearchResult('2','Книга [litres]','Автор'),SearchResult('3','Книга. Том I','Автор')],[],(query,))
    class Catalog:
        seen=[]
        async def details(self,book_id):
            self.seen.append(book_id)
            return BookDetails(book_id,'Книга. Том I' if book_id=='3' else 'Книга',['Автор'],[],[],[],[],None,None,'Описание',[],'https://catalog.test/b/'+book_id)
    async def scenario():
        catalog=Catalog()
        async with browser(tmp_path,Service(),catalog) as client:
            soup=BeautifulSoup(await (await client.get('/search?q=Книга')).text(),'html.parser')
            links=soup.select('a.card-title')
            assert len(links)==1 and links[0]['href'].startswith('/work/')
            target=links[0]['href']
            card=BeautifulSoup(await (await client.get(target)).text(),'html.parser')
            assert catalog.seen==['1']
            choice=card.find('a',string='Издания и тома')
            selector=BeautifulSoup(await (await client.get(choice['href'])).text(),'html.parser')
            third=next(a for a in selector.find_all('a') if 'edition=3' in a.get('href',''))
            await client.get(third['href'])
            assert catalog.seen==['1','3']
            assert (await client.get(target+'?edition=999')).status==400
            from app.web.app import DEPS_KEY
            deps=client.server.app[DEPS_KEY]
            await deps.access_repo.ensure_user(43,'approved')
            code=await deps.web_access_repo.create_pairing_code(43)
            client.session.cookie_jar.clear()
            await client.post('/pair',data={'code':code})
            assert (await client.get(target)).status==410
            assert catalog.seen==['1','3']
    asyncio.run(scenario())


def test_web_author_catalog_continues_after_fifth_page(tmp_path):
    from app.services.works import AuthorWorksPage
    class Catalog:
        calls=[]
        async def author_works_page(self,author_id,*,offset=0,limit=40):
            self.calls.append((offset,limit))
            return AuthorWorksPage('Автор',[SearchResult(str(i),f'Книга {i:03}','Автор') for i in range(offset,min(113,offset+limit))],113,min(113,offset+limit),offset+limit<113)
    async def scenario():
        catalog=Catalog()
        async with browser(tmp_path,object(),catalog) as client:
            soup=BeautifulSoup(await (await client.get('/author/9?page=4')).text(),'html.parser')
            next_page=soup.find('a',string='Дальше →')
            assert next_page['href']=='/author/9?page=5'
            following=await (await client.get(next_page['href'])).text()
            assert '/book/40' in following and '6/15' in following
            assert catalog.calls==[(32,8),(40,8)]
    asyncio.run(scenario())


def test_web_two_name_typos_offer_confirmation_without_claiming_catalog_absence(tmp_path):
    from app.services.search import SearchService
    class Catalog:
        async def search(self,*args,**kwargs): raise AssertionError('no fragment search')
        async def search_authors(self,*args,**kwargs): raise AssertionError('no lookup before confirmation')
    async def scenario():
        async with browser(tmp_path,SearchService(Catalog(),book_limit=40,author_limit=20)) as client:
            response=await client.get('/search',params={'q':'Леа Толстнй'})
            soup=BeautifulSoup(await response.text(),'html.parser')
            assert response.status==200 and soup.find('a',string='Лев Толстой')
            assert 'Автор не найден' not in soup.get_text() and 'Ничего не нашлось' not in soup.get_text()
    asyncio.run(scenario())
