from __future__ import annotations
from dataclasses import dataclass
import json
import httpx
@dataclass(frozen=True)
class BookIntent:
 kind:str; search_queries:list[str]; reply:str
class AiAssistant:
 def __init__(self,api_key:str|None,model:str,enabled:bool=False): self.api_key=api_key; self.model=model; self.enabled=enabled and bool(api_key)
 async def understand(self,text:str)->BookIntent:
  if not self.enabled: return BookIntent('search',[text],'Ищу подходящие варианты.')
  prompt='''Ты помощник книжного бота. Верни только JSON: {"kind":"search|recommend","search_queries":[...],"reply":"короткая русская фраза"}. Преврати запрос пользователя в 1-3 лаконичных поисковых запроса для каталога книг. Не выдумывай книги.'''
  async with httpx.AsyncClient(timeout=20) as client:
   r=await client.post('https://api.openai.com/v1/responses',headers={'Authorization':f'Bearer {self.api_key}'},json={'model':self.model,'instructions':prompt,'input':text})
   r.raise_for_status(); payload=r.json(); raw=payload.get('output_text') or _extract_text(payload)
  try:
   data=json.loads(raw); queries=[str(q).strip() for q in data.get('search_queries',[]) if str(q).strip()][:3]
   return BookIntent(str(data.get('kind','search')),queries or [text],str(data.get('reply') or 'Ищу подходящие варианты.'))
  except Exception:
   return BookIntent('search',[text],'Ищу подходящие варианты.')
def _extract_text(payload):
 out=[]
 for item in payload.get('output',[]):
  for c in item.get('content',[]):
   if c.get('type') in {'output_text','text'}: out.append(c.get('text',''))
 return ''.join(out)
