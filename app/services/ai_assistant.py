from __future__ import annotations
from dataclasses import dataclass
import json
import httpx
import logging
from app.repositories.cache import CacheRepository
from app.services.search_logic import norm
logger=logging.getLogger(__name__)

@dataclass(frozen=True)
class BookIntent:
 kind:str; search_queries:list[str]; reply:str; negative_keywords:list[str]; topic:str

INTENT_SCHEMA={
 'type':'json_schema','name':'book_intent','strict':True,
 'schema':{
  'type':'object','additionalProperties':False,
  'properties':{
   'kind':{'type':'string','enum':['search','recommend']},
   'search_queries':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':12},
   'reply':{'type':'string'},
   'negative_keywords':{'type':'array','items':{'type':'string'},'maxItems':8},
   'topic':{'type':'string'},
  },
  'required':['kind','search_queries','reply','negative_keywords','topic'],
 }
}

class AiAssistant:
 def __init__(self,api_key:str|None,model:str,enabled:bool=False,cache_repo:CacheRepository|None=None,cache_ttl_seconds:int=86400): self.api_key=api_key; self.model=model; self.enabled=enabled and bool(api_key); self.cache_repo=cache_repo; self.cache_ttl_seconds=cache_ttl_seconds
 async def understand(self,text:str,*,force_recommend:bool=False,topic:str|None=None,intent:str|None=None)->BookIntent:
  if not self.enabled: return BookIntent('search',[text],'Ищу подходящие варианты.',[],'')
  cache_key=(f'ai_intent:{self.model}:{int(force_recommend)}:{norm(text)}' if topic is None and intent is None else f'ai_intent:{self.model}:{int(force_recommend)}:{norm(topic or text)}:{intent or ""}')
  if self.cache_repo:
   try:
    cached=await self.cache_repo.get(cache_key)
    if cached: return BookIntent(**cached)
   except Exception: logger.warning('AI intent cache read failed',exc_info=True)
  prompt='''Ты помощник книжного каталога. Твоя задача — превратить фразу пользователя в 1-3 КОРОТКИХ запроса, которые реально стоит отправить в библиотечный поиск.

Правила:
- Для точного запроса верни название книги или автора без лишних слов.
- Для рекомендации верни конкретные поисковые зацепки, которые каталог может найти.
- Для recommendation верни до 12 разных author/title anchors; для search достаточно до 3.
- Для жанровых запросов выбирай известных авторов, циклы и книги, которые вероятно есть в русскоязычном каталоге.
- Для зарубежной литературы предпочитай имена авторов в русском написании; названия книг добавляй только если они достаточно уникальны.
- Каталог русскоязычный. Все имена авторов и названия книг в search_queries возвращай в общепринятом русском написании, даже если оригинал зарубежный.
- Не возвращай английские названия вроде "City of Glass" или "Paul Auster", если есть обычная русская форма "Город стекла" и "Пол Остер".
- Никогда не возвращай исходную длинную фразу целиком, если это рекомендация.
- Никогда не используй как search_queries общие слова-инструкции: «подборка», «книга», «книги», «литература», «хорошего», «что почитать».
- Если пользователь просит «классику российского постмодерна», хорошие запросы выглядят как «Пелевин», «Сорокин», «Москва-Петушки», а не как исходное предложение.
- Если пользователь просит зарубежную литературу, хорошие запросы выглядят как «Пол Остер», «Харуки Мураками», «Марк Данилевский». Не начинай с неоднозначного названия книги, если оно легко даст ложные совпадения.
- Для «попаданцев» используй якоря: «Артем Каменистый», «Константин Муравьев», «Владимир Поселягин», «Михаил Ланцов», «Андрей Круз», «Сварог Бушков».
- Для «немецкого постмодерна» используй якоря: «Патрик Зюскинд», «Томас Бернхард», «В. Г. Зебальд», «Петер Хандке», «Эльфрида Елинек», «Гюнтер Грасс».
- reply — одна короткая естественная фраза на русском, без обещаний того, чего ещё не найдено.'''
  if force_recommend:
   prompt += '\n\nЭто точно запрос на рекомендацию. kind должен быть "recommend". Верни только имена авторов или уникальные названия конкретных книг, не повторяй исходную фразу.'
  try:
   async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
    r=await client.post('https://api.openai.com/v1/responses',headers={'Authorization':f'Bearer {self.api_key}'},json={'model':self.model,'instructions':prompt,'input':f'Исходный запрос: {text}\nТема для поиска: {topic or text}\nIntent: {intent or ""}','text':{'format':INTENT_SCHEMA}})
    r.raise_for_status(); payload=r.json(); raw=payload.get('output_text') or _extract_text(payload)
  except Exception:
   logger.warning('AI intent request failed; falling back to plain search',exc_info=True)
   return BookIntent('search',[text],'AI сейчас отвечает медленно. Ищу обычным способом.',[],'')
  try:
   data=json.loads(raw); kind=str(data.get('kind','search')); limit=12 if kind=='recommend' else 3; queries=[str(q).strip() for q in data.get('search_queries',[]) if str(q).strip()][:limit]
   if not queries: raise ValueError('empty queries')
   result=BookIntent(kind,queries,str(data.get('reply') or 'Ищу подходящие варианты.'),[str(x) for x in data.get('negative_keywords',[])],str(data.get('topic','')))
   if self.cache_repo:
    try: await self.cache_repo.set(cache_key,'ai_intent',result,self.cache_ttl_seconds)
    except Exception: logger.warning('AI intent cache write failed',exc_info=True)
   return result
  except Exception:
   return BookIntent('search',[text],'Ищу подходящие варианты.',[],'')

def _extract_text(payload):
 out=[]
 for item in payload.get('output',[]):
  for c in item.get('content',[]):
   if c.get('type') in {'output_text','text'}: out.append(c.get('text',''))
 return ''.join(out)
