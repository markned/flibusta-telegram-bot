from __future__ import annotations
from dataclasses import dataclass
import json
import httpx
import logging
logger=logging.getLogger(__name__)

@dataclass(frozen=True)
class BookIntent:
 kind:str; search_queries:list[str]; reply:str

INTENT_SCHEMA={
 'type':'json_schema','name':'book_intent','strict':True,
 'schema':{
  'type':'object','additionalProperties':False,
  'properties':{
   'kind':{'type':'string','enum':['search','recommend']},
   'search_queries':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':3},
   'reply':{'type':'string'},
  },
  'required':['kind','search_queries','reply'],
 }
}

class AiAssistant:
 def __init__(self,api_key:str|None,model:str,enabled:bool=False): self.api_key=api_key; self.model=model; self.enabled=enabled and bool(api_key)
 async def understand(self,text:str)->BookIntent:
  if not self.enabled: return BookIntent('search',[text],'Ищу подходящие варианты.')
  prompt='''Ты помощник книжного каталога. Твоя задача — превратить фразу пользователя в 1-3 КОРОТКИХ запроса, которые реально стоит отправить в библиотечный поиск.

Правила:
- Для точного запроса верни название книги или автора без лишних слов.
- Для рекомендации верни конкретные поисковые зацепки, которые каталог может найти.
- Для зарубежной литературы предпочитай имена авторов в русском написании; названия книг добавляй только если они достаточно уникальны.
- Каталог русскоязычный. Все имена авторов и названия книг в search_queries возвращай в общепринятом русском написании, даже если оригинал зарубежный.
- Не возвращай английские названия вроде "City of Glass" или "Paul Auster", если есть обычная русская форма "Город стекла" и "Пол Остер".
- Никогда не возвращай исходную длинную фразу целиком, если это рекомендация.
- Если пользователь просит «классику российского постмодерна», хорошие запросы выглядят как «Пелевин», «Сорокин», «Москва-Петушки», а не как исходное предложение.
- Если пользователь просит зарубежную литературу, хорошие запросы выглядят как «Пол Остер», «Харуки Мураками», «Марк Данилевский». Не начинай с неоднозначного названия книги, если оно легко даст ложные совпадения.
- reply — одна короткая естественная фраза на русском, без обещаний того, чего ещё не найдено.'''
  try:
   async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
    r=await client.post('https://api.openai.com/v1/responses',headers={'Authorization':f'Bearer {self.api_key}'},json={'model':self.model,'instructions':prompt,'input':text,'text':{'format':INTENT_SCHEMA}})
    r.raise_for_status(); payload=r.json(); raw=payload.get('output_text') or _extract_text(payload)
  except Exception:
   logger.warning('AI intent request failed; falling back to plain search',exc_info=True)
   return BookIntent('search',[text],'AI сейчас отвечает медленно. Ищу обычным способом.')
  try:
   data=json.loads(raw); queries=[str(q).strip() for q in data.get('search_queries',[]) if str(q).strip()][:3]
   if not queries: raise ValueError('empty queries')
   return BookIntent(str(data.get('kind','search')),queries,str(data.get('reply') or 'Ищу подходящие варианты.'))
  except Exception:
   return BookIntent('search',[text],'Ищу подходящие варианты.')

def _extract_text(payload):
 out=[]
 for item in payload.get('output',[]):
  for c in item.get('content',[]):
   if c.get('type') in {'output_text','text'}: out.append(c.get('text',''))
 return ''.join(out)
