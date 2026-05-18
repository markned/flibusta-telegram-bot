from __future__ import annotations
from dataclasses import dataclass
from uuid import uuid4
from app.flibusta import AuthorResult

@dataclass(frozen=True)
class SearchSession:
 session_id:str; user_id:int; chat_id:int; query:str; title:str|None; page:int; results:list
@dataclass(frozen=True)
class AuthorSession:
 session_id:str; user_id:int; chat_id:int; query:str; page:int; authors:list[AuthorResult]
search_sessions:dict[str,SearchSession]={}
author_sessions:dict[str,AuthorSession]={}
retry_sessions:dict[str,str]={}
search_timestamps:dict[int,list[float]]={}

def create_search_session(user_id:int,chat_id:int,query:str,results:list,title:str|None=None)->SearchSession:
 prune_sessions(search_sessions); session=SearchSession(uuid4().hex[:10],user_id,chat_id,query,title,0,results); search_sessions[session.session_id]=session; return session

def create_author_session(user_id:int,chat_id:int,query:str,authors:list[AuthorResult])->AuthorSession:
 prune_sessions(author_sessions); session=AuthorSession(uuid4().hex[:10],user_id,chat_id,query,0,authors); author_sessions[session.session_id]=session; return session

def prune_sessions(storage:dict[str,object])->None:
 if len(storage)>100:
  for key in list(storage.keys())[:20]: storage.pop(key,None)
