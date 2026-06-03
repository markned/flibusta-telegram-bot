from __future__ import annotations
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from app.repositories.access import AccessRepository
class AccessMiddleware(BaseMiddleware):
 def __init__(self,repo:AccessRepository,admin_ids:set[int]): self.repo=repo; self.admin_ids=admin_ids
 async def __call__(self,handler:Callable[[Any,dict[str,Any]],Awaitable[Any]],event:Any,data:dict[str,Any]):
  user=getattr(event,'from_user',None)
  if user is None or user.id in self.admin_ids: return await handler(event,data)
  if isinstance(event,Message) and (event.text or '').startswith('/start'): return await handler(event,data)
  if isinstance(event,CallbackQuery) and (event.data or '').startswith('access_'): return await handler(event,data)
  access=await self.repo.get_user(user.id)
  if access and access.status=='approved': return await handler(event,data)
  text='Доступ к боту пока не открыт. Нажми Start в профиле бота, чтобы запросить приглашение.' if access is None else 'Запрос уже отправлен. Я сообщу, когда админ откроет доступ.'
  if isinstance(event,CallbackQuery):
   await event.answer('Доступ пока не открыт',show_alert=True)
  else: await event.answer(text)
