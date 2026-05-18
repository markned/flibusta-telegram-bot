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
  def json(self): return {'output_text':'{"kind":"recommend","search_queries":["Пелевин","Сорокин"],"reply":"Вот с чего можно начать.","negative_keywords":[],"topic":""}'}
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
  def json(self): return {'output_text':'{"kind":"recommend","search_queries":["Пол Остер","Город стекла"],"reply":"Вот с чего можно начать.","negative_keywords":[],"topic":""}'}
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
  def json(self): return {'output_text':'{"kind":"recommend","search_queries":["Пол Остер","Харуки Мураками"],"reply":"Вот несколько направлений.","negative_keywords":[],"topic":""}'}
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

def test_recommendation_pack_popadantsy():
 from app.services.recommendation_packs import get_recommendation_pack
 assert 'Артем Каменистый' in get_recommendation_pack('книга о попаданцах')

def test_recommendation_pack_german_postmodern():
 from app.services.recommendation_packs import get_recommendation_pack
 assert 'Патрик Зюскинд' in get_recommendation_pack('немецкий постмодерн от первого лица')

def test_bad_recommendation_filter():
 from app.services.recommendation_filters import is_bad_recommendation_candidate
 assert is_bad_recommendation_candidate('Инструкция по написанию бестселлера о попаданцах','книга о попаданцах',[])
 assert not is_bad_recommendation_candidate('Инструкция по резьбе','нужна инструкция',[])

def test_merge_recommendation_queries_dedupes():
 from app.services.recommendations import merge_recommendation_queries
 assert merge_recommendation_queries(['Пелевин'],['Пелевин','Сорокин'],6)==['Пелевин','Сорокин']

def test_query_analysis_new_recommendation_patterns():
 assert analyze_query('книга о попаданцах').recommendation_like
 assert analyze_query('немецкий постмодерн от первого лица').recommendation_like

def test_ai_intent_cache_hit_avoids_api_call(tmp_path:Path,monkeypatch):
 from app.services.ai_assistant import AiAssistant, BookIntent
 db=Database(str(tmp_path/'db.sqlite')); run(db.initialize()); repo=CacheRepository(db)
 run(repo.set('ai_intent:gpt-5-nano:0:книга о попаданцах','ai_intent',BookIntent('recommend',['Артем Каменистый'],'Готово',[],'попаданцы'),60))
 def boom(*a,**kw): raise AssertionError('api should not be called')
 monkeypatch.setattr('app.services.ai_assistant.httpx.AsyncClient',boom)
 result=run(AiAssistant('key','gpt-5-nano',True,cache_repo=repo).understand('книга о попаданцах'))
 assert result.search_queries==['Артем Каменистый']

def test_antiutopia_pack_has_broad_fallback():
 from app.services.recommendation_packs import get_recommendation_pack
 pack=get_recommendation_pack('антиутопия')
 assert {'Оруэлл','Кобо Абэ','Стругацкие'}.issubset(set(pack))

def test_query_analysis_author_name_is_not_recommendation():
 assert not analyze_query('Эдит Патту').recommendation_like
 assert analyze_query('Эдит Патту').likely_author

class _FakeUser:
 def __init__(self,user_id=501): self.id=user_id; self.username='u'; self.full_name='User'
class _FakeChat:
 id=777
class _FakeBot:
 async def send_chat_action(self,*a,**kw): return None
class _FakeMessage:
 def __init__(self,text=''):
  self.text=text; self.from_user=_FakeUser(); self.chat=_FakeChat(); self.bot=_FakeBot(); self.answers=[]; self.edits=[]
 async def answer(self,text,*a,**kw): self.answers.append((text,kw)); return self
 async def edit_text(self,text,*a,**kw): self.edits.append(text); return self

def test_send_search_results_sends_message(monkeypatch):
 import app.main as main
 class Flib:
  async def search(self,q,limit): return [SearchResult('1','Мастер и Маргарита','Михаил Булгаков')]
 monkeypatch.setattr(main,'flibusta',Flib()); main.search_timestamps.clear()
 msg=_FakeMessage()
 run(main.send_search_results(msg,'мастер и маргарита'))
 assert any('Книги' in text for text,_ in msg.answers)

def test_text_routing_exact_uses_smart_not_ai(monkeypatch):
 import app.main as main
 calls=[]
 async def no_author(*a,**kw): return False
 async def smart(*a,**kw): calls.append('smart'); return True
 async def ai(*a,**kw): calls.append('ai')
 monkeypatch.setattr(main,'send_author_title_results',no_author); monkeypatch.setattr(main,'send_reversed_author_title_results',no_author); monkeypatch.setattr(main,'send_smart_results',smart); monkeypatch.setattr(main,'send_ai_results',ai)
 run(main.search_text(_FakeMessage('Эдит Патту')))
 assert calls==['smart']

def test_text_routing_recommendation_uses_ai(monkeypatch):
 import app.main as main
 calls=[]
 async def no_author(*a,**kw): return False
 async def smart(*a,**kw): calls.append('smart'); return True
 async def ai(*a,**kw): calls.append('ai')
 monkeypatch.setattr(main,'send_author_title_results',no_author); monkeypatch.setattr(main,'send_reversed_author_title_results',no_author); monkeypatch.setattr(main,'send_smart_results',smart); monkeypatch.setattr(main,'send_ai_results',ai)
 run(main.search_text(_FakeMessage('антиутопия')))
 assert calls==['ai']

def test_reversed_author_title_search_finds_title(monkeypatch):
 import app.main as main
 class Flib:
  async def search(self,q,limit):
   assert q=='Исповедь'; return [SearchResult('1','Исповедь','Лев Толстой')]
 monkeypatch.setattr(main,'flibusta',Flib())
 msg=_FakeMessage()
 assert run(main.send_reversed_author_title_results(msg,'Исповедь Толстой')) is True
 assert any('Исповедь' in text for text,_ in msg.answers)

def test_ai_exception_falls_back_to_smart(monkeypatch):
 import app.main as main
 calls=[]
 async def boom(*a,**kw): raise RuntimeError('no ai')
 async def smart(*a,**kw): calls.append('smart'); return True
 monkeypatch.setattr(main.ai_assistant,'understand',boom); monkeypatch.setattr(main,'send_smart_results',smart)
 run(main.send_ai_results(_FakeMessage(),'книга о попаданцах'))
 assert calls==['smart']

def test_recommendation_filters_bad_literal_and_caps_details(monkeypatch):
 import app.main as main
 from app.services.ai_assistant import BookIntent
 from app.flibusta import BookDetails
 details_calls=[]
 class Flib:
  async def search_all(self,q,book_limit,author_limit):
   if q=='книга о попаданцах':
    return [SearchResult('bad','Инструкция по написанию бестселлера о попаданцах','Автор')],[]
   return [SearchResult(q,q,f'Автор {q}')],[]
  async def author_books(self,*a,**kw): return ('',[])
  async def details(self,book_id):
   details_calls.append(book_id)
   return BookDetails(book_id=book_id,title=book_id,authors=['Автор'],author_refs=[],translators=[],illustrators=[],genres=[],file_size=None,pages=None,annotation='Описание',formats=[],page_url='x')
 async def intent(*a,**kw):
  return BookIntent('recommend',['книга о попаданцах','Артем Каменистый','Константин Муравьев','Владимир Поселягин','Михаил Ланцов','Андрей Круз'],'Подбираю.',[],'')
 monkeypatch.setattr(main,'flibusta',Flib())
 monkeypatch.setattr(main.ai_assistant,'understand',intent)
 monkeypatch.setattr(main.settings,'ai_recommendation_max_details',2)
 monkeypatch.setattr(main.settings,'ai_recommendation_min_results',2)
 monkeypatch.setattr(main.settings,'ai_recommendation_target_results',4)
 msg=_FakeMessage()
 run(main.send_ai_results(msg,'книга о попаданцах'))
 assert 'bad' not in details_calls
 assert len(details_calls)==2

def test_kindle_button_is_not_silent():
 import app.main as main
 msg=_FakeMessage('⚙️ Kindle')
 run(main.search_text(msg))
 assert msg.answers
