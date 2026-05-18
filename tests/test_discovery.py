import asyncio
from pathlib import Path
import pytest
from app.flibusta import SearchResult
from app.repositories.db import Database
from app.repositories.cache import CacheRepository
from app.services.discovery.types import BookIdea, WebSearchResult
from app.services.discovery.web_search import DiscoveryWebError, DisabledWebSearchProvider, TavilyWebSearchProvider
from app.services.discovery.flibusta_matcher import FlibustaMatcher
from app.services.discovery.recommender import DiscoveryRateLimiter, DiscoveryRecommender

def run(c): return asyncio.run(c)

class _Resp:
 def __init__(self,payload): self.payload=payload
 def raise_for_status(self): pass
 def json(self): return self.payload
class _Client:
 def __init__(self,captured,payload,fail=False): self.captured=captured; self.payload=payload; self.fail=fail
 async def __aenter__(self): return self
 async def __aexit__(self,*a): pass
 async def post(self,*a,**kw):
  if self.fail: raise RuntimeError('boom')
  self.captured.update(kw['json']); return _Resp(self.payload)

def test_tavily_provider_parses_and_truncates(monkeypatch):
 captured={}
 monkeypatch.setattr('app.services.discovery.web_search.httpx.AsyncClient',lambda timeout:_Client(captured,{'results':[{'title':'A','url':'u','content':'x'*20}]}))
 result=run(TavilyWebSearchProvider('secret',max_snippet_chars=5).search('q',3))
 assert captured['api_key']=='secret' and captured['query']=='q' and captured['max_results']==3
 assert result[0].snippet=='xxxxx'
 assert 'secret' not in repr(result)

def test_tavily_provider_raises_safe_error(monkeypatch):
 monkeypatch.setattr('app.services.discovery.web_search.httpx.AsyncClient',lambda timeout:_Client({}, {}, fail=True))
 with pytest.raises(DiscoveryWebError): run(TavilyWebSearchProvider('secret').search('q',3))

class _Ideas:
 def __init__(self,ideas): self.ideas=ideas; self.calls=[]
 async def generate(self,query,**kw): self.calls.append(kw); return self.ideas
class _Web:
 def __init__(self): self.calls=0
 async def search(self,query,limit): self.calls+=1; return [WebSearchResult('1984','u','classic','tavily')]
class _Flib:
 def __init__(self): self.calls=[]
 async def search(self,q,limit=5):
  self.calls.append(q)
  if q in {'Оруэлл 1984','1984'}: return [SearchResult('1','1984','Джордж Оруэлл'),SearchResult('1','1984','Джордж Оруэлл')]
  if q=='bad': return [SearchResult('2','Инструкция по написанию романа','Автор')]
  return []

async def _make_recommender(tmp_path,ideas,web=None,limiter=None):
 db=Database(str(tmp_path/'db.sqlite')); await db.initialize(); cache=CacheRepository(db)
 flib=_Flib(); gen=_Ideas(ideas)
 return DiscoveryRecommender(flibusta=flib,cache_repo=cache,idea_generator=gen,matcher=FlibustaMatcher(flib,max_checks=8,max_final_results=10),web_provider=web or DisabledWebSearchProvider(),cache_ttl_seconds=60,max_web_results=5,web_enabled=web is not None,rate_limiter=limiter), flib, gen, cache

def test_discovery_returns_only_matched_books_and_dedupes(tmp_path):
 rec,_,_,_=run(_make_recommender(tmp_path,[BookIdea('1984','Оруэлл','1984','fits','model'),BookIdea(None,None,'bad',None,'model')]))
 result=run(rec.recommend(1,'антиутопия','recommend',False))
 assert [b.book_id for b in result.books]==['1']

def test_discovery_web_cache_avoids_second_call(tmp_path):
 web=_Web(); rec,_,_,_=run(_make_recommender(tmp_path,[BookIdea('1984','Оруэлл','1984',None,'web')],web=web))
 assert run(rec.recommend(1,'антиутопия','discover',True)).books
 assert run(rec.recommend(2,'антиутопия','discover',True)).books
 assert web.calls==1

def test_discovery_rate_limit_skips_web(tmp_path):
 web=_Web(); limiter=DiscoveryRateLimiter(0,0); rec,_,gen,_=run(_make_recommender(tmp_path,[BookIdea('1984','Оруэлл','1984',None,'model')],web=web,limiter=limiter))
 result=run(rec.recommend(1,'антиутопия','discover',True))
 assert result.note=='web_rate_limited' and web.calls==0 and gen.calls[0]['web_results']==[]

class _FakeUser:
 id=9
class _FakeChat:
 id=99
class _FakeBot:
 async def send_chat_action(self,*a,**kw): pass
class _Msg:
 def __init__(self): self.from_user=_FakeUser(); self.chat=_FakeChat(); self.bot=_FakeBot(); self.answers=[]
 async def answer(self,text,*a,**kw): self.answers.append(text); return self
 async def edit_text(self,text,*a,**kw): self.answers.append(text); return self

def test_recommend_command_does_not_use_web_by_default(monkeypatch):
 import app.main as main
 seen=[]
 async def discovery(*a,**kw): seen.append(kw['use_web']); return True
 monkeypatch.setattr(main,'send_discovery_results',discovery)
 run(main.recommend_command(_Msg(), type('C',(),{'args':'антиутопия'})()))
 assert seen==[False]

def test_admin_discovery_status_hides_key(monkeypatch):
 import app.main as main
 async def stats(): return (0,{},0)
 monkeypatch.setattr(main.settings,'admin_user_ids','9')
 monkeypatch.setattr(main.settings,'discovery_web_api_key','secret-key')
 monkeypatch.setattr(main.cache_repo,'stats',stats)
 msg=_Msg(); run(main.admin_discovery_status(msg))
 assert msg.answers and 'secret-key' not in msg.answers[-1] and 'api key present: yes' in msg.answers[-1]
