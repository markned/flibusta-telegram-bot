from __future__ import annotations
from app.services.discovery.types import BookIdea, MatchedBook
from app.services.recommendation_filters import is_bad_recommendation_candidate
from app.services.search_logic import base_title, norm, rank_and_dedupe_books

class FlibustaMatcher:
 def __init__(self,flibusta,*,max_checks:int=8,max_final_results:int=10): self.flibusta=flibusta; self.max_checks=max_checks; self.max_final_results=max_final_results
 async def match(self,ideas:list[BookIdea],query:str)->list[MatchedBook]:
  found=[]; seen=set()
  for idea in ideas[:self.max_checks]:
   for search_query in _queries_for(idea):
    matched=False
    books=rank_and_dedupe_books(await self.flibusta.search(search_query,limit=5), search_query)
    for book in books:
     if book.book_id in seen or is_bad_recommendation_candidate(book.title,query,[]): continue
     score=_score(idea,book.title,book.author)
     if score <= 0: continue
     seen.add(book.book_id); found.append(MatchedBook(book.book_id,book.title,book.author,idea.why_it_may_fit,idea.source,score)); matched=True
     break
    if matched: break
   if len(found)>=self.max_final_results: break
  return sorted(found,key=lambda b:b.score,reverse=True)[:self.max_final_results]

def _queries_for(idea:BookIdea)->list[str]:
 result=[]
 if idea.author and idea.title: result.append(f'{idea.author} {idea.title}')
 for candidate in (idea.search_query_ru,idea.title):
  if candidate and candidate not in result: result.append(candidate)
 return result[:2]
def _score(idea:BookIdea,title:str,author:str|None)->float:
 title_n=norm(base_title(title)); idea_title=norm(idea.title or ''); author_n=norm(author or ''); idea_author=norm(idea.author or '')
 score=0.1
 if idea_title:
  if title_n==idea_title: score+=3
  elif idea_title in title_n or title_n in idea_title: score+=2
  else: score-=0.2
 if idea_author:
  if idea_author in author_n or author_n in idea_author: score+=2
  else: score-=1
 return score
