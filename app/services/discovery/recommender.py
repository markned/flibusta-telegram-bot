from __future__ import annotations
import asyncio
from collections import defaultdict, deque
from datetime import UTC, datetime
import logging
from app.repositories.cache import CacheRepository
from app.services.discovery.types import DiscoveryResult, MatchedBook, WebSearchResult
from app.services.discovery.web_search import DisabledWebSearchProvider, DiscoveryWebError, WebSearchProvider
from app.services.search_logic import norm
logger=logging.getLogger(__name__)

class DiscoveryRateLimiter:
 def __init__(self,user_daily:int,global_daily:int): self.user_daily=user_daily; self.global_daily=global_daily; self.by_user=defaultdict(deque); self.global_hits=deque()
 def allow(self,user_id:int)->bool:
  now=datetime.now(UTC).timestamp(); cutoff=now-86400
  _prune(self.global_hits,cutoff); _prune(self.by_user[user_id],cutoff)
  if len(self.global_hits)>=self.global_daily or len(self.by_user[user_id])>=self.user_daily: return False
  self.global_hits.append(now); self.by_user[user_id].append(now); return True

def _prune(items,cutoff):
 while items and items[0]<cutoff: items.popleft()

class DiscoveryRecommender:
 def __init__(self,*,flibusta,cache_repo:CacheRepository,idea_generator,matcher,web_provider:WebSearchProvider|None=None,favorites_repo=None,history_repo=None,preferences_repo=None,cache_ttl_seconds:int=604800,max_web_results:int=5,web_enabled:bool=False,rate_limiter:DiscoveryRateLimiter|None=None,concurrency:int=1):
  self.flibusta=flibusta; self.cache_repo=cache_repo; self.idea_generator=idea_generator; self.matcher=matcher; self.web_provider=web_provider or DisabledWebSearchProvider(); self.favorites_repo=favorites_repo; self.history_repo=history_repo; self.preferences_repo=preferences_repo; self.cache_ttl_seconds=cache_ttl_seconds; self.max_web_results=max_web_results; self.web_enabled=web_enabled; self.rate_limiter=rate_limiter; self.semaphore=asyncio.Semaphore(max(1,concurrency))
 async def recommend(self,user_id:int,query:str,mode:str,use_web:bool)->DiscoveryResult:
  async with self.semaphore:
   return await self._recommend(user_id,query,mode,use_web)
 async def _recommend(self,user_id:int,query:str,mode:str,use_web:bool)->DiscoveryResult:
  key=f'discovery_result:{user_id}:{norm(query)}:{mode}:{int(use_web)}'
  try:
   cached=await self.cache_repo.get(key)
   if cached: return DiscoveryResult(query=cached['query'],mode=cached['mode'],books=[MatchedBook(**b) for b in cached['books']],note=cached.get('note'),used_web=bool(cached.get('used_web',False)))
  except Exception: logger.warning('discovery result cache read failed',exc_info=True)
  note=None; web_results=[]
  if use_web and self.web_enabled:
   if self.rate_limiter and not self.rate_limiter.allow(user_id):
    note='web_rate_limited'
   else:
    web_results=await self._web_results(query)
  profile=await self._profile(user_id)
  ideas=await self.idea_generator.generate(query,mode=mode,profile=profile,web_results=web_results)
  books=await self.matcher.match(ideas,query)
  result=DiscoveryResult(query,mode,books,note,bool(web_results))
  if books:
   try: await self.cache_repo.set(key,'discovery_result',result,self.cache_ttl_seconds)
   except Exception: logger.warning('discovery result cache write failed',exc_info=True)
  return result
 async def _web_results(self,query:str):
  key=f'discovery_web:{norm(query)}:{type(self.web_provider).__name__}'
  try:
   cached=await self.cache_repo.get(key)
   if cached: return [WebSearchResult(**item) for item in cached]
  except Exception: logger.warning('discovery web cache read failed',exc_info=True)
  try: results=await self.web_provider.search(_web_query(query),self.max_web_results)
  except DiscoveryWebError:
   logger.info('web discovery failed safely'); return []
  try: await self.cache_repo.set(key,'discovery_web',results,self.cache_ttl_seconds)
  except Exception: logger.warning('discovery web cache write failed',exc_info=True)
  return results
 async def _profile(self,user_id:int)->str:
  authors=[]; seen=[]
  if self.favorites_repo:
   authors.extend(item.author for item in await self.favorites_repo.list(user_id,limit=4) if item.author)
  if self.history_repo:
   recent=await self.history_repo.recent(user_id,limit=4)
   authors.extend(item.author for item in recent if item.author); seen.extend(item.book_id for item in recent)
  pref=await self.preferences_repo.get(user_id) if self.preferences_repo else None
  compact=', '.join(dict.fromkeys(authors))[:120]
  return f'authors={compact or "—"}; format={(pref.preferred_download_format if pref else None) or "—"}; seen={",".join(seen[:4]) or "—"}'
def _web_query(query:str)->str:
 return f'лучшие книги {query}'
