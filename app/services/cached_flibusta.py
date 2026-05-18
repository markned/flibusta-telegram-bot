from __future__ import annotations
import logging
from app.flibusta import AuthorResult, BookDetails, DownloadFormat, FlibustaClient, SearchResult, SeriesRef
from app.repositories.cache import CacheRepository
logger=logging.getLogger(__name__)
class CachedFlibustaClient:
 def __init__(self,client:FlibustaClient,repo:CacheRepository,*,enabled:bool,ttls:dict[str,int]): self.client=client; self.repo=repo; self.enabled=enabled; self.ttls=ttls
 async def close(self): await self.client.close()
 async def search(self,query:str,limit:int=8): return await self._cached('book_search',f'{query.lower()}:{limit}',lambda:self.client.search(query,limit),lambda rows:[SearchResult(**r) for r in rows])
 async def search_authors(self,query:str,limit:int=20): return await self._cached('author_search',f'{query.lower()}:{limit}',lambda:self.client.search_authors(query,limit),lambda rows:[AuthorResult(**r) for r in rows])
 async def search_all(self,query:str,book_limit:int=8,author_limit:int=20):
  return await self._cached('smart_search',f'{query.lower()}:{book_limit}:{author_limit}',lambda:self.client.search_all(query,book_limit,author_limit),lambda pair:([SearchResult(**r) for r in pair[0]],[AuthorResult(**r) for r in pair[1]]))
 async def author_books(self,author_id:str,limit:int=40): return await self._cached('author_books',f'{author_id}:{limit}',lambda:self.client.author_books(author_id,limit),lambda pair:(pair[0],[SearchResult(**r) for r in pair[1]]))
 async def details(self,book_id:str): return await self._cached('book_details',book_id,lambda:self.client.details(book_id),_details_from_dict)
 async def download(self,*a,**kw): return await self.client.download(*a,**kw)
 async def _cached(self,typ,key,loader,decode):
  cache_key=f'{typ}:{key}'
  if self.enabled:
   try:
    hit=await self.repo.get(cache_key)
    if hit is not None: return decode(hit)
   except Exception: logger.warning('cache read failed type=%s',typ,exc_info=True)
  value=await loader()
  if self.enabled:
   try: await self.repo.set(cache_key,typ,value,self.ttls[typ])
   except Exception: logger.warning('cache write failed type=%s',typ,exc_info=True)
  return value

def _details_from_dict(d):
 return BookDetails(**{**d,'author_refs':[AuthorResult(**x) for x in d['author_refs']],'formats':[DownloadFormat(**x) for x in d['formats']],'series':[SeriesRef(**x) for x in d.get('series',[])]})
