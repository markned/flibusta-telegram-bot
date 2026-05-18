import asyncio
from pathlib import Path
from app.repositories.db import Database
from app.repositories.cache import CacheRepository
from app.repositories.favorites import FavoritesRepository
from app.repositories.download_history import DownloadHistoryRepository
from app.repositories.last_books import LastBooksRepository
from app.services.query_analyzer import analyze_query
from app.services.cached_flibusta import CachedFlibustaClient
from app.flibusta import SearchResult, AuthorResult

def run(c): return asyncio.run(c)

def test_query_analysis():
 a=analyze_query('"Мастер и Маргарита" epub')
 assert a.quoted_title and a.cleaned=='"Мастер и Маргарита"' and a.format_hint=='epub'
 assert analyze_query('Лев Толстой').likely_author
 b=analyze_query('Лев Толстой - Война и мир')
 assert b.author_part=='Лев Толстой' and b.title_part=='Война и мир'

def test_cache_hit_and_cleanup(tmp_path:Path):
 db=Database(str(tmp_path/'db.sqlite')); run(db.initialize()); repo=CacheRepository(db)
 run(repo.set('x','book_search',[{'book_id':'1'}],60)); assert run(repo.get('x'))==[{'book_id':'1'}]
 total,by_type,expired=run(repo.stats()); assert total==1 and by_type['book_search']==1 and expired==0
 run(repo.set('old','book_search',[], -1)); assert run(repo.get('old')) is None; assert run(repo.clear())==1

def test_favorites_history_and_last_book(tmp_path:Path):
 db=Database(str(tmp_path/'db.sqlite')); run(db.initialize())
 fav=FavoritesRepository(db); hist=DownloadHistoryRepository(db); last=LastBooksRepository(db)
 run(fav.add(1,'7','Book','Author')); run(fav.add(1,'7','Book','Author'))
 assert run(fav.count(1))==1 and run(fav.exists(1,'7'))
 run(hist.add(user_id=1,book_id='7',title='Book',author='Author',format='epub',filename='b.epub',file_size_bytes=4,delivery_target='telegram',status='sent'))
 assert run(hist.recent(1))[0].title=='Book'
 run(last.upsert(1,'7','Book','Author','opened')); assert run(last.get(1)).book_id=='7'
 assert run(fav.remove(1,'7'))==1

class CountingFlibusta:
 def __init__(self): self.calls=0
 async def search(self,q,limit=8): self.calls+=1; return [SearchResult('1','Book','Author')]
 async def close(self): pass

def test_cached_client_uses_cached_search(tmp_path:Path):
 db=Database(str(tmp_path/'db.sqlite')); run(db.initialize()); raw=CountingFlibusta(); cached=CachedFlibustaClient(raw,CacheRepository(db),enabled=True,ttls={'book_search':60})
 assert run(cached.search('Book'))[0].title=='Book'; assert run(cached.search('Book'))[0].title=='Book'; assert raw.calls==1

def test_access_invite_and_approval(tmp_path:Path):
 from app.repositories.access import AccessRepository
 db=Database(str(tmp_path/'db.sqlite')); run(db.initialize()); repo=AccessRepository(db)
 run(repo.request_access(1,'u','User')); assert run(repo.get_user(1)).status=='pending'
 run(repo.set_status(1,'approved',99)); assert run(repo.get_user(1)).status=='approved'
 code=run(repo.create_invite(99,1)); assert run(repo.redeem_invite(code,2,'v','Visitor')) is True
 assert run(repo.get_user(2)).status=='approved'; assert run(repo.redeem_invite(code,3,'w','Other')) is False

def test_ai_assistant_disabled_falls_back():
 from app.services.ai_assistant import AiAssistant
 result=run(AiAssistant(None,'gpt-5-nano',False).understand('что-то как Дюна'))
 assert result.search_queries==['что-то как Дюна']

def test_ai_assistant_parses_structured_queries(monkeypatch):
 from app.services.ai_assistant import AiAssistant
 class Resp:
  def raise_for_status(self): pass
  def json(self): return {'output_text':'{"kind":"recommend","search_queries":["Пелевин","Сорокин"],"reply":"Вот с чего можно начать."}'}
 class Client:
  async def __aenter__(self): return self
  async def __aexit__(self,*a): pass
  async def post(self,*a,**kw): return Resp()
 monkeypatch.setattr('app.services.ai_assistant.httpx.AsyncClient', lambda timeout: Client())
 result=run(AiAssistant('key','gpt-5-nano',True).understand('Хочу классику российского постмодерна'))
 assert result.search_queries==['Пелевин','Сорокин']

def test_ai_prompt_requests_russian_search_queries(monkeypatch):
 from app.services.ai_assistant import AiAssistant
 captured={}
 class Resp:
  def raise_for_status(self): pass
  def json(self): return {'output_text':'{"kind":"recommend","search_queries":["Пол Остер","Город стекла"],"reply":"Вот с чего можно начать."}'}
 class Client:
  async def __aenter__(self): return self
  async def __aexit__(self,*a): pass
  async def post(self,*a,**kw):
   captured.update(kw['json']); return Resp()
 monkeypatch.setattr('app.services.ai_assistant.httpx.AsyncClient', lambda timeout: Client())
 result=run(AiAssistant('key','gpt-5-nano',True).understand('зарубежный постмодерн'))
 assert result.search_queries==['Пол Остер','Город стекла']
 assert 'Каталог русскоязычный' in captured['instructions']

def test_access_user_management(tmp_path:Path):
 from app.repositories.access import AccessRepository
 db=Database(str(tmp_path/'db.sqlite')); run(db.initialize()); repo=AccessRepository(db)
 run(repo.ensure_user(7,'approved',1)); assert run(repo.count_by_status())['approved']==1
 run(repo.ensure_user(7,'blocked',1)); assert run(repo.get_user(7)).status=='blocked'
 assert run(repo.delete_user(7))==1

def test_ai_prompt_prefers_foreign_author_queries(monkeypatch):
 from app.services.ai_assistant import AiAssistant
 captured={}
 class Resp:
  def raise_for_status(self): pass
  def json(self): return {'output_text':'{"kind":"recommend","search_queries":["Пол Остер","Харуки Мураками"],"reply":"Вот несколько направлений."}'}
 class Client:
  async def __aenter__(self): return self
  async def __aexit__(self,*a): pass
  async def post(self,*a,**kw): captured.update(kw['json']); return Resp()
 monkeypatch.setattr('app.services.ai_assistant.httpx.AsyncClient', lambda timeout: Client())
 result=run(AiAssistant('key','gpt-5-nano',True).understand('зарубежный постмодерн от первого лица'))
 assert result.search_queries==['Пол Остер','Харуки Мураками']
 assert 'предпочитай имена авторов' in captured['instructions']

def test_recommendation_books_are_interleaved():
 from app.main import _interleave_book_groups
 a=SearchResult('1','A','Author A'); b=SearchResult('2','B','Author A'); c=SearchResult('3','C','Author C'); d=SearchResult('4','D','Author C')
 assert [x.book_id for x in _interleave_book_groups([[a,b],[c,d]])]==['1','3','2','4']

def test_recommendation_details_text_includes_short_descriptions():
 from app.ui.library import recommendation_details_text
 from app.flibusta import BookDetails
 book=SearchResult('1','Книга','Автор')
 details=BookDetails(book_id='1',title='Книга',authors=['Автор'],author_refs=[],translators=[],illustrators=[],genres=[],file_size=None,pages=None,annotation='Очень длинное описание книги. '*20,formats=[],page_url='x')
 text=recommendation_details_text('запрос',[(book,details)])
 assert '<b>1. Книга</b>' in text and 'Автор' in text and '…' in text

def test_query_analysis_author_title_without_separator():
 a=analyze_query('Лев Толстой исповедь')
 assert a.author_part=='Лев Толстой' and a.title_part=='исповедь'

def test_query_analysis_recommendation_like():
 assert analyze_query('Классика российского постмодерна').recommendation_like
 assert analyze_query('зарубежный известный постмодерн').recommendation_like

def test_recommendation_fallback_queries():
 from app.main import _recommendation_fallback_queries
 assert _recommendation_fallback_queries('Классика российского постмодерна')[:2]==['Пелевин','Сорокин']
 assert _recommendation_fallback_queries('зарубежный известный постмодерн')[0]=='Пол Остер'
