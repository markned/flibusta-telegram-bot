from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
import secrets
from app.repositories.db import Database

def now(): return datetime.now(UTC).isoformat()
@dataclass(frozen=True)
class AccessUser:
 user_id:int; username:str|None; full_name:str|None; status:str; requested_at:str; approved_at:str|None; approved_by:int|None
@dataclass(frozen=True)
class InviteCode:
 code:str; created_by:int; max_uses:int; uses:int; created_at:str; expires_at:str|None; revoked_at:str|None
class AccessRepository:
 def __init__(self,db:Database): self.db=db
 async def get_user(self,user_id:int):
  async with self.db.connect() as c: r=await (await c.execute('SELECT * FROM access_users WHERE user_id=?',(user_id,))).fetchone()
  return None if r is None else AccessUser(**dict(r))
 async def request_access(self,user_id:int,username:str|None,full_name:str|None):
  async with self.db.connect() as c:
   await c.execute("INSERT INTO access_users(user_id,username,full_name,status,requested_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,full_name=excluded.full_name",(user_id,username,full_name,'pending',now())); await c.commit()
 async def set_status(self,user_id:int,status:str,approved_by:int|None=None):
  async with self.db.connect() as c:
   await c.execute('UPDATE access_users SET status=?, approved_at=?, approved_by=? WHERE user_id=?',(status,now() if status=='approved' else None,approved_by,user_id)); await c.commit()
 async def create_invite(self,created_by:int,max_uses:int=1):
  code=secrets.token_urlsafe(8).replace('-','').replace('_','')[:10]
  async with self.db.connect() as c:
   await c.execute('INSERT INTO invite_codes(code,created_by,max_uses,uses,created_at) VALUES(?,?,?,?,?)',(code,created_by,max_uses,0,now())); await c.commit()
  return code
 async def redeem_invite(self,code:str,user_id:int,username:str|None,full_name:str|None):
  async with self.db.connect() as c:
   row=await (await c.execute('SELECT * FROM invite_codes WHERE code=? AND revoked_at IS NULL',(code,))).fetchone()
   if row is None or row['uses']>=row['max_uses']: return False
   await c.execute("INSERT INTO access_users(user_id,username,full_name,status,requested_at,approved_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,full_name=excluded.full_name,status='approved',approved_at=excluded.approved_at",(user_id,username,full_name,'approved',now(),now()))
   await c.execute('UPDATE invite_codes SET uses=uses+1 WHERE code=?',(code,)); await c.execute('INSERT INTO invite_uses(code,user_id,used_at) VALUES(?,?,?)',(code,user_id,now())); await c.commit(); return True
 async def approved_count(self):
  async with self.db.connect() as c: r=await (await c.execute("SELECT COUNT(*) FROM access_users WHERE status='approved'")).fetchone()
  return int(r[0])
 async def count_by_status(self):
  async with self.db.connect() as c: rows=await (await c.execute("SELECT status,COUNT(*) AS count FROM access_users GROUP BY status")).fetchall()
  return {r['status']:int(r['count']) for r in rows}
 async def list_users(self,status:str|None=None,limit:int=10):
  sql='SELECT * FROM access_users'; params=()
  if status: sql+=' WHERE status=?'; params=(status,)
  sql+=' ORDER BY requested_at DESC LIMIT ?'; params+= (limit,)
  async with self.db.connect() as c: rows=await (await c.execute(sql,params)).fetchall()
  return [AccessUser(**dict(r)) for r in rows]
 async def ensure_user(self,user_id:int,status:str='approved',approved_by:int|None=None):
  async with self.db.connect() as c:
   await c.execute("INSERT INTO access_users(user_id,status,requested_at,approved_at,approved_by) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,approved_at=excluded.approved_at,approved_by=excluded.approved_by",(user_id,status,now(),now() if status=='approved' else None,approved_by)); await c.commit()
 async def delete_user(self,user_id:int):
  async with self.db.connect() as c: cur=await c.execute('DELETE FROM access_users WHERE user_id=?',(user_id,)); await c.commit(); return cur.rowcount
 async def list_invites(self,limit:int=10):
  async with self.db.connect() as c: rows=await (await c.execute('SELECT * FROM invite_codes ORDER BY created_at DESC LIMIT ?',(limit,))).fetchall()
  return [InviteCode(**dict(r)) for r in rows]
 async def revoke_invite(self,code:str):
  async with self.db.connect() as c: cur=await c.execute('UPDATE invite_codes SET revoked_at=? WHERE code=? AND revoked_at IS NULL',(now(),code)); await c.commit(); return cur.rowcount
