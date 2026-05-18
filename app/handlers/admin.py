from __future__ import annotations
from html import escape
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.repositories.access import AccessRepository
from app.repositories.cache import CacheRepository
from app.repositories.download_history import DownloadHistoryRepository
from app.repositories.favorites import FavoritesRepository
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.services.kindle_queue import KindleQueue

def build_admin_router(*,access_repo:AccessRepository,cache_repo:CacheRepository,history_repo:DownloadHistoryRepository,favorites_repo:FavoritesRepository,deliveries_repo:KindleDeliveriesRepository,kindle_queue:KindleQueue,admin_ids:set[int])->Router:
 router=Router()
 def is_admin(msg): return msg.from_user and msg.from_user.id in admin_ids
 async def panel(message:Message,*,edit=False):
  counts=await access_repo.count_by_status(); total,_,expired=await cache_repo.stats()
  text=(
   '<b>Админка</b>\n\n'
   f"Пользователи: {counts.get('approved',0)} активных · {counts.get('pending',0)} ждут · {counts.get('blocked',0)} заблокированы\n"
   f"Избранное: {await favorites_repo.count()}\n"
   f"Кэш: {total} записей · {expired} просрочено\n"
   f"Kindle: {kindle_queue.active_jobs} активных · {await deliveries_repo.count_recent_failures(24)} ошибок за 24ч"
  )
  kb=InlineKeyboardBuilder()
  kb.row(InlineKeyboardButton(text='👥 Пользователи',callback_data='admin_users'),InlineKeyboardButton(text='⏳ Заявки',callback_data='admin_pending'))
  kb.row(InlineKeyboardButton(text='🎟 Инвайты',callback_data='admin_invites'),InlineKeyboardButton(text='📊 Статистика',callback_data='admin_stats_home'))
  kb.row(InlineKeyboardButton(text='🧹 Очистить кэш',callback_data='admin_cache_clear_expired'))
  if edit: await message.edit_text(text,reply_markup=kb.as_markup())
  else: await message.answer(text,reply_markup=kb.as_markup())
 @router.message(Command('admin'))
 async def admin_home(message:Message):
  if is_admin(message): await panel(message)
 @router.callback_query(F.data=='admin_home')
 async def admin_home_cb(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  await c.answer(); await panel(c.message,edit=True)
 @router.callback_query(F.data.in_({'admin_users','admin_pending'}))
 async def users_cb(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  await c.answer(); status='pending' if c.data=='admin_pending' else None; users=await access_repo.list_users(status=status,limit=10)
  lines=['<b>Заявки на доступ</b>' if status else '<b>Пользователи</b>']
  kb=InlineKeyboardBuilder()
  for u in users:
   label=(u.full_name or u.username or str(u.user_id))[:30]; lines.append(f"{u.user_id} — {escape(label)} — {u.status}")
   if u.status=='pending': kb.row(InlineKeyboardButton(text=f'✅ {label}',callback_data=f'admin_user_approve:{u.user_id}'),InlineKeyboardButton(text='❌',callback_data=f'admin_user_block:{u.user_id}'))
   else: kb.row(InlineKeyboardButton(text=f'🚫 {label}',callback_data=f'admin_user_block:{u.user_id}'),InlineKeyboardButton(text='🗑',callback_data=f'admin_user_delete:{u.user_id}'))
  kb.row(InlineKeyboardButton(text='← Админка',callback_data='admin_home'))
  await c.message.edit_text('\n'.join(lines) if users else '<b>Пользователи</b>\n\nПока пусто.',reply_markup=kb.as_markup())
 @router.message(Command('admin_user_add'))
 async def user_add(m:Message,command:CommandObject):
  if not is_admin(m):return
  arg=(command.args or '').strip()
  if not arg.isdigit(): await m.answer('Использование: /admin_user_add 123456'); return
  await access_repo.ensure_user(int(arg),'approved',m.from_user.id); await m.answer('Пользователь добавлен.')
 @router.message(Command('admin_user_remove'))
 async def user_remove(m:Message,command:CommandObject):
  if not is_admin(m):return
  arg=(command.args or '').strip()
  if not arg.isdigit(): await m.answer('Использование: /admin_user_remove 123456'); return
  await access_repo.delete_user(int(arg)); await m.answer('Пользователь удалён.')
 @router.callback_query(F.data.startswith('admin_user_'))
 async def user_action(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  prefix,uid=c.data.rsplit(':',1); uid=int(uid)
  if prefix.endswith('approve'): await access_repo.set_status(uid,'approved',c.from_user.id); note='Доступ открыт.'
  elif prefix.endswith('block'): await access_repo.ensure_user(uid,'blocked',c.from_user.id); note='Пользователь заблокирован.'
  else: await access_repo.delete_user(uid); note='Пользователь удалён.'
  await c.answer(note); await panel(c.message,edit=True)
 @router.callback_query(F.data=='admin_invites')
 async def invites(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  await c.answer(); items=await access_repo.list_invites(); kb=InlineKeyboardBuilder(); lines=['<b>Инвайты</b>']
  for i in items:
   state='отозван' if i.revoked_at else f'{i.uses}/{i.max_uses}'
   lines.append(f'<code>{i.code}</code> — {state}')
   if not i.revoked_at: kb.row(InlineKeyboardButton(text=f'Отозвать {i.code}',callback_data=f'admin_invite_revoke:{i.code}'))
  kb.row(InlineKeyboardButton(text='Создать 1',callback_data='admin_invite_new:1'),InlineKeyboardButton(text='Создать 5',callback_data='admin_invite_new:5'))
  kb.row(InlineKeyboardButton(text='← Админка',callback_data='admin_home'))
  await c.message.edit_text('\n'.join(lines),reply_markup=kb.as_markup())
 @router.callback_query(F.data.startswith('admin_invite_new:'))
 async def invite_new(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  uses=int(c.data.rsplit(':',1)[1]); code=await access_repo.create_invite(c.from_user.id,uses); me=await c.bot.get_me(); await c.answer('Создано'); await c.message.answer(f'https://t.me/{me.username}?start=invite_{code}')
 @router.callback_query(F.data.startswith('admin_invite_revoke:'))
 async def invite_revoke(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  await access_repo.revoke_invite(c.data.rsplit(':',1)[1]); await c.answer('Отозвано'); await invites(c)
 @router.callback_query(F.data=='admin_cache_clear_expired')
 async def clear_cache(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  deleted=await cache_repo.clear(); await c.answer(f'Удалено {deleted}'); await panel(c.message,edit=True)
 @router.callback_query(F.data=='admin_stats_home')
 async def stats(c:CallbackQuery):
  if c.from_user.id not in admin_ids:return
  await c.answer(); top=', '.join(f'{f}:{n}' for f,n in await history_repo.top_formats()) or '—'
  await c.message.edit_text('<b>Статистика</b>\n\n'+f"Telegram сегодня: {await history_repo.sent_today('telegram')}\nKindle сегодня: {await history_repo.sent_today('kindle')}\nТоп форматов: {top}",reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text='← Админка',callback_data='admin_home')).as_markup())
 return router
