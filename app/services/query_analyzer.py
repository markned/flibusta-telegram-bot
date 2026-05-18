from __future__ import annotations
from dataclasses import dataclass
import re
FORMAT_HINTS={'epub','fb2','pdf','mobi','txt'}
@dataclass(frozen=True)
class QueryAnalysis:
 original:str; cleaned:str; quoted_title:bool; likely_author:bool; format_hint:str|None; author_part:str|None; title_part:str|None; has_year_or_series:bool; recommendation_like:bool

def analyze_query(query:str,max_words:int=12)->QueryAnalysis:
 original=query.strip(); quoted=bool(re.search(r'["«“][^"»”]+["»”]',original)); hint=None
 words=original.split()
 kept=[]
 for w in words[:max_words]:
  bare=re.sub(r'[^A-Za-zА-Яа-я0-9]+','',w).lower()
  if bare in FORMAT_HINTS and hint is None: hint=bare; continue
  kept.append(w)
 cleaned=re.sub(r'\s+',' ',' '.join(kept)).strip()
 author=title=None
 for sep in (' - ',': '):
  if sep in cleaned:
   left,right=cleaned.split(sep,1)
   if _looks_person(left): author,title=left.strip(),right.strip()
   elif _looks_person(right): author,title=right.strip(),left.strip()
   break
 if author is None and title is None and len(kept) >= 3 and _looks_person(' '.join(kept[:2])):
  author=' '.join(kept[:2]); title=' '.join(kept[2:])
 likely=_looks_person(cleaned) and not quoted
 has_marker=bool(re.search(r'\b(?:18|19|20)\d{2}\b|#\d+|\bкн\.?\s*\d+',cleaned,re.I))
 recommendation_like=bool(re.search(r'\b(какой-нибудь|посовет|подбери|похож|классика|известн|зарубежн|мрачн|современн|постмодерн|фантастик)\w*',cleaned,re.I))
 return QueryAnalysis(original,cleaned,quoted,likely,hint,author,title,has_marker,recommendation_like)

def _looks_person(text:str)->bool:
 parts=[p for p in re.split(r'\s+',text.strip()) if p]
 return 2 <= len(parts) <= 4 and all(re.fullmatch(r'[A-Za-zА-Яа-яЁё-]+',p) for p in parts)
