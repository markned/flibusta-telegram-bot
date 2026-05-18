from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from app.repositories.db import Database

def now(): return datetime.now(UTC).isoformat()
@dataclass(frozen=True)
class LastBook:
 user_id:int; book_id:str; title:str; author:str|None; source:str; updated_at:str
class LastBooksRepository:
 def __init__(self,db:Database): self.db=db
 async def upsert(self,user_id:int,book_id:str,title:str,author:str|None,source:str):
  async with self.db.connect() as c:
   await c.execute('''INSERT INTO user_last_books VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET book_id=excluded.book_id,title=excluded.title,author=excluded.author,source=excluded.source,updated_at=excluded.updated_at''',(user_id,book_id,title,author,source,now())); await c.commit()
 async def get(self,user_id:int):
  async with self.db.connect() as c: row=await (await c.execute('SELECT * FROM user_last_books WHERE user_id=?',(user_id,))).fetchone()
  return None if row is None else LastBook(**dict(row))
