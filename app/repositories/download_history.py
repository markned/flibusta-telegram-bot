from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from app.repositories.db import Database

def now(): return datetime.now(UTC).isoformat()
@dataclass(frozen=True)
class DownloadHistoryItem:
 id:int; user_id:int; book_id:str; title:str|None; author:str|None; format:str; filename:str|None; file_size_bytes:int|None; delivery_target:str; status:str; created_at:str; error:str|None
class DownloadHistoryRepository:
 def __init__(self,db:Database): self.db=db
 async def add(self,*,user_id:int,book_id:str,title:str|None,author:str|None,format:str,filename:str|None,file_size_bytes:int|None,delivery_target:str,status:str,error:str|None=None):
  async with self.db.connect() as c:
   await c.execute('INSERT INTO download_history(user_id,book_id,title,author,format,filename,file_size_bytes,delivery_target,status,created_at,error) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(user_id,book_id,title,author,format,filename,file_size_bytes,delivery_target,status,now(),error)); await c.commit()
 async def recent(self,user_id:int,*,status:str='sent',limit:int=10):
  async with self.db.connect() as c:
   rows=await (await c.execute('SELECT * FROM download_history WHERE user_id=? AND status=? ORDER BY created_at DESC,id DESC LIMIT ?',(user_id,status,limit))).fetchall()
  return [DownloadHistoryItem(**dict(r)) for r in rows]
 async def count_recent_downloads(self,user_id:int,hours:int=1):
  since=(datetime.now(UTC)-timedelta(hours=hours)).isoformat()
  async with self.db.connect() as c: row=await (await c.execute("SELECT COUNT(*) FROM download_history WHERE user_id=? AND delivery_target='telegram' AND created_at>=?",(user_id,since))).fetchone()
  return int(row[0])
 async def sent_today(self,target:str):
  since=datetime.now(UTC).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
  async with self.db.connect() as c: row=await (await c.execute('SELECT COUNT(*) FROM download_history WHERE delivery_target=? AND status=\'sent\' AND created_at>=?',(target,since))).fetchone()
  return int(row[0])
 async def top_formats(self,limit:int=5):
  async with self.db.connect() as c: rows=await (await c.execute("SELECT format,COUNT(*) AS count FROM download_history WHERE status='sent' GROUP BY format ORDER BY count DESC LIMIT ?",(limit,))).fetchall()
  return [(r['format'],int(r['count'])) for r in rows]
