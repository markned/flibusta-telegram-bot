from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from app.repositories.db import Database

def now(): return datetime.now(UTC).isoformat()
@dataclass(frozen=True)
class UserPreference:
 user_id:int; preferred_download_format:str|None; preferred_kindle_format:str
class UserPreferencesRepository:
 def __init__(self,db:Database): self.db=db
 async def get(self,user_id:int):
  async with self.db.connect() as c:
   r=await (await c.execute('SELECT * FROM user_preferences WHERE user_id=?',(user_id,))).fetchone()
  return None if r is None else UserPreference(r['user_id'],r['preferred_download_format'],r['preferred_kindle_format'])
 async def upsert(self,user_id:int,*,download_format:str|None=None,kindle_format:str|None=None):
  existing=await self.get(user_id); t=now(); d=download_format if download_format is not None else (existing.preferred_download_format if existing else None); k=kindle_format or (existing.preferred_kindle_format if existing else 'epub')
  async with self.db.connect() as c:
   await c.execute('INSERT INTO user_preferences VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET preferred_download_format=excluded.preferred_download_format, preferred_kindle_format=excluded.preferred_kindle_format, updated_at=excluded.updated_at',(user_id,d,k,t,t)); await c.commit()
  return UserPreference(user_id,d,k)
 async def all_rows(self):
  async with self.db.connect() as c: return await (await c.execute('SELECT * FROM user_preferences ORDER BY user_id')).fetchall()
 async def import_json_once(self,path:Path):
  if not path.exists(): return 0
  data=json.loads(path.read_text()); count=0
  for uid,prefs in data.items():
   if str(uid).isdigit(): await self.upsert(int(uid),download_format=prefs.get('preferred_format')); count+=1
  path.rename(path.with_name(path.name+'.migrated')); return count
