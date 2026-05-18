from __future__ import annotations
import re
from app.flibusta import AuthorResult

def clean_query(query:str)->str:
 cleaned=query.replace('ё','е').replace('Ё','Е'); cleaned=re.sub(r'[«»"“”„]+','',cleaned); return re.sub(r'\s+',' ',cleaned).strip()
def norm(text:str)->str:
 text=clean_query(text).lower(); text=re.sub(r'\[[^\]]+\]|\([^)]*\)','',text); text=re.sub(r'[^a-zа-я0-9]+',' ',text,flags=re.I); return re.sub(r'\s+',' ',text).strip()
def base_title(title:str)->str: return re.sub(r'\s*(\[[^\]]+\]|\([^)]*\))','',title).strip()
def rank_and_dedupe_books(results:list,query:str)->list:
 q=norm(query); deduped={}
 for item in results:
  key=(norm(base_title(item.title)),norm(item.author or '')); current=deduped.get(key)
  if current is None or book_score(item,q)>book_score(current,q): deduped[key]=item
 return sorted(deduped.values(),key=lambda item:book_score(item,q),reverse=True)
def book_score(item,q:str)->tuple[int,int,int]:
 title=norm(base_title(item.title)); full=norm(item.title); return (int(title==q),int(title.startswith(q)),int(q in full))
def rank_authors(authors:list[AuthorResult],query:str)->list[AuthorResult]:
 q=norm(query); return sorted(authors,key=lambda item:(norm(item.name)==q,q in norm(item.name)),reverse=True)
def fallback_queries(query:str)->list[str]:
 words=[w for w in re.split(r'\s+',query) if w]; candidates=[]
 for size in (4,3,2,1):
  if len(words)>=size:
   candidate=' '.join(words[:size])
   if candidate!=query and candidate not in candidates:candidates.append(candidate)
 return candidates
