from __future__ import annotations
from dataclasses import dataclass
from time import time
from uuid import uuid4

@dataclass(frozen=True)
class PendingRecommendation:
    pending_id:str; user_id:int; chat_id:int; original_query:str; intent_kind:str; topic:str; use_web:bool; mode:str; created_at:float

class PendingRecommendationStore:
 def __init__(self,ttl_seconds:int=900,max_count:int=100): self.ttl_seconds=ttl_seconds; self.max_count=max_count; self.items={}
 def create(self,*,user_id:int,chat_id:int,original_query:str,intent_kind:str,topic:str,use_web:bool,mode:str)->PendingRecommendation:
  self.prune(); pid=uuid4().hex[:10]
  if len(self.items)>=self.max_count:
   oldest=min(self.items.values(),key=lambda item:item.created_at); self.items.pop(oldest.pending_id,None)
  item=PendingRecommendation(pid,user_id,chat_id,original_query,intent_kind,topic,use_web,mode,time()); self.items[pid]=item; return item
 def get(self,pid:str)->PendingRecommendation|None:
  self.prune(); return self.items.get(pid)
 def delete(self,pid:str): return self.items.pop(pid,None)
 def prune(self):
  cutoff=time()-self.ttl_seconds
  for pid,item in list(self.items.items()):
   if item.created_at<cutoff: self.items.pop(pid,None)
