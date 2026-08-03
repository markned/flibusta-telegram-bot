from __future__ import annotations
from difflib import SequenceMatcher
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
def book_score(item,q:str)->tuple[int,int,int,int,int,float]:
 title=norm(base_title(item.title)); full=norm(item.title); author=norm(item.author or '')
 q_tokens=set(q.split()); title_tokens=set(title.split()); combined_tokens=set(f'{title} {author}'.split())
 title_coverage=_coverage(q_tokens,title_tokens); combined_coverage=_coverage(q_tokens,combined_tokens)
 similarity=SequenceMatcher(None,q,title).ratio() if q and title else 0.0
 return (
  int(title==q),
  int(title.startswith(q)),
  int(q in full),
  int(title_coverage==100),
  combined_coverage,
  similarity,
 )
def rank_authors(authors:list[AuthorResult],query:str)->list[AuthorResult]:
 q=norm(query); return sorted(authors,key=lambda item:(norm(item.name)==q,q in norm(item.name)),reverse=True)
def fallback_queries(query:str)->list[str]:
 words=[w for w in re.split(r'\s+',query) if w]; candidates=[]
 if len(words)>1:
  candidates.extend((' '.join(words[:-1]),' '.join(words[1:])))
 for size in (4,3,2,1):
  if len(words)>=size:
   candidate=' '.join(words[:size])
   if candidate!=query:candidates.append(candidate)
 result=[]
 for candidate in candidates:
  candidate=candidate.strip()
  if candidate and candidate!=query and candidate not in result: result.append(candidate)
 return result

def _coverage(expected:set[str],actual:set[str])->int:
 if not expected:return 0
 return round(100*len(expected & actual)/len(expected))
