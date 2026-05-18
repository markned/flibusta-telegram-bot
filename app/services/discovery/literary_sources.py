from __future__ import annotations
from app.services.discovery.types import BookIdea
class LiterarySourceProvider:
 async def find_book_ideas(self,query:str,limit:int)->list[BookIdea]: raise NotImplementedError
class DisabledLiterarySourceProvider(LiterarySourceProvider):
 async def find_book_ideas(self,query:str,limit:int)->list[BookIdea]: return []
