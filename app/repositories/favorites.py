from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from app.repositories.db import Database

def now(): return datetime.now(UTC).isoformat()
@dataclass(frozen=True)
class Favorite:
 id:int; user_id:int; book_id:str; title:str; author:str|None; created_at:str
class FavoritesRepository:
 def __init__(self,db:Database): self.db=db
 async def add(self,user_id:int,book_id:str,title:str,author:str|None):
  async with self.db.connect() as c:
   await c.execute('INSERT OR IGNORE INTO user_favorites(user_id,book_id,title,author,created_at) VALUES(?,?,?,?,?)',(user_id,book_id,title,author,now())); await c.commit()
 async def remove(self,user_id:int,book_id:str):
  async with self.db.connect() as c:
   cur=await c.execute('DELETE FROM user_favorites WHERE user_id=? AND book_id=?',(user_id,book_id)); await c.commit(); return cur.rowcount
 async def exists(self,user_id:int,book_id:str):
  async with self.db.connect() as c: row=await (await c.execute('SELECT 1 FROM user_favorites WHERE user_id=? AND book_id=?',(user_id,book_id))).fetchone()
  return row is not None
 async def list(self,user_id:int,limit:int=8,offset:int=0):
  async with self.db.connect() as c:
   rows=await (await c.execute('SELECT * FROM user_favorites WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(user_id,limit,offset))).fetchall()
  return [Favorite(**dict(r)) for r in rows]
 async def count(self,user_id:int|None=None):
  async with self.db.connect() as c:
   row=await (await c.execute('SELECT COUNT(*) FROM user_favorites' + (' WHERE user_id=?' if user_id is not None else ''), (() if user_id is None else (user_id,)))).fetchone()
  return int(row[0])
