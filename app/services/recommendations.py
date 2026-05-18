from __future__ import annotations
from app.services.recommendation_filters import is_bad_recommendation_candidate
from app.services.search_logic import norm
def merge_recommendation_queries(ai_queries:list[str],pack:list[str],limit:int)->list[str]:
 result=[]; seen=set()
 for item in [*ai_queries,*pack]:
  key=norm(item)
  if key and key not in seen: seen.add(key); result.append(item)
  if len(result)>=limit: break
 return result
