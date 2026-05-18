from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
import logging
import re
import httpx
from app.repositories.cache import CacheRepository
from app.services.discovery.types import BookIdea, WebSearchResult
from app.services.recommendation_packs import get_recommendation_pack
from app.services.search_logic import norm
logger=logging.getLogger(__name__)

IDEAS_SCHEMA={
 'type':'json_schema','name':'book_ideas','strict':True,
 'schema':{'type':'object','additionalProperties':False,'properties':{
  'ideas':{'type':'array','maxItems':12,'items':{'type':'object','additionalProperties':False,'properties':{
   'title':{'type':['string','null']},'author':{'type':['string','null']},'search_query_ru':{'type':'string'},
   'why_it_may_fit':{'type':['string','null']},'source':{'type':'string','enum':['model','web','user_profile']},
  },'required':['title','author','search_query_ru','why_it_may_fit','source']}},
 },'required':['ideas']}
}

class BookIdeaGenerator:
 def __init__(self,api_key:str|None,model:str,enabled:bool,*,cache_repo:CacheRepository|None=None,cache_ttl_seconds:int=604800,max_ideas:int=12,timeout_seconds:float=15):
  self.api_key=api_key; self.model=model; self.enabled=enabled and bool(api_key); self.cache_repo=cache_repo; self.cache_ttl_seconds=cache_ttl_seconds; self.max_ideas=max_ideas; self.timeout_seconds=timeout_seconds
 async def generate(self,query:str,*,mode:str,profile:str='',web_results:list[WebSearchResult]|None=None)->list[BookIdea]:
  web_results=web_results or []; web_hash=_web_hash(web_results); cache_key=f'discovery_ideas:{norm(query)}:{mode}:{self.model}:{web_hash}'
  if self.cache_repo:
   try:
    cached=await self.cache_repo.get(cache_key)
    if cached: return [BookIdea(**item) for item in cached]
   except Exception: logger.warning('discovery ideas cache read failed',exc_info=True)
  ideas=[]
  if self.enabled:
   ideas=await self._from_model(query,profile,web_results)
  if not ideas and web_results:
   ideas=_heuristic_web_ideas(web_results)
  if not ideas:
   ideas=[BookIdea(None,None,item,None,'model') for item in get_recommendation_pack(query)]
  ideas=ideas[:self.max_ideas]
  if self.cache_repo and ideas:
   try: await self.cache_repo.set(cache_key,'discovery_ideas',ideas,self.cache_ttl_seconds)
   except Exception: logger.warning('discovery ideas cache write failed',exc_info=True)
  return ideas
 async def _from_model(self,query:str,profile:str,web_results:list[WebSearchResult])->list[BookIdea]:
  snippets='\n'.join(f'- {r.title}: {r.snippet}' for r in web_results[:5])
  instructions='''Ты помогаешь собрать идеи книг для последующей проверки в библиотечном каталоге. Верни только реальные идеи книг. Не утверждай, что они есть в каталоге. Не выдумывай id. Для русскоязычного каталога search_query_ru пиши в общепринятом русском написании. Если даны веб-фрагменты, извлекай только идеи, поддержанные ими. Коротко, без длинных объяснений.'''
  user_input=f'Запрос: {query}\nПрофиль: {profile or "—"}\nВеб-фрагменты:\n{snippets or "—"}'
  try:
   async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds,connect=5.0)) as client:
    response=await client.post('https://api.openai.com/v1/responses',headers={'Authorization':f'Bearer {self.api_key}'},json={'model':self.model,'instructions':instructions,'input':user_input,'text':{'format':IDEAS_SCHEMA}})
    response.raise_for_status(); payload=response.json(); raw=payload.get('output_text') or _extract_text(payload); data=json.loads(raw)
   return [BookIdea(**item) for item in data.get('ideas',[]) if str(item.get('search_query_ru') or '').strip()][:self.max_ideas]
  except Exception:
   logger.warning('book idea generation failed',exc_info=True); return []

def _web_hash(items:list[WebSearchResult])->str:
 raw='|'.join(f'{r.title}:{r.snippet}' for r in items)
 return hashlib.sha1(raw.encode()).hexdigest()[:12] if raw else 'none'
def _heuristic_web_ideas(items:list[WebSearchResult])->list[BookIdea]:
 result=[]
 for item in items:
  title=re.split(r'\s+[—|-]\s+',item.title,1)[0].strip()
  if title: result.append(BookIdea(title,None,title,None,'web'))
 return result
def _extract_text(payload):
 return ''.join(c.get('text','') for item in payload.get('output',[]) for c in item.get('content',[]) if c.get('type') in {'output_text','text'})
