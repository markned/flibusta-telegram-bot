from __future__ import annotations
from app.services.search_logic import norm
BAD=('инструкция','пособие','учебник','руководство','как написать','методичка')
def is_bad_recommendation_candidate(title:str,query:str,negative_keywords:list[str])->bool:
 text=norm(title); q=norm(query)
 if any(norm(k) in text for k in negative_keywords): return True
 non_fiction=any(word in q for word in ('инструкц','учебник','пособ','руководств','как написать'))
 return (not non_fiction) and any(norm(word) in text for word in BAD)
def merge_recommendation_queries(ai_queries:list[str],pack:list[str],limit:int)->list[str]:
 result=[]; seen=set()
 for item in [*ai_queries,*pack]:
  key=norm(item)
  if key and key not in seen: seen.add(key); result.append(item)
  if len(result)>=limit: break
 return result
