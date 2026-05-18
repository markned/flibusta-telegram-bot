from __future__ import annotations
from html import escape
from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.flibusta import BookDetails
from app.pagination import SEARCH_PAGE_SIZE, page_items, total_pages
from app.repositories.download_history import DownloadHistoryItem
from app.state import SearchSession, AuthorSession

def search_results_text(session:SearchSession,title:str|None=None)->str:
 total=len(session.results); pages=total_pages(total); start=session.page*SEARCH_PAGE_SIZE+1; end=min(total,(session.page+1)*SEARCH_PAGE_SIZE)
 heading=title or session.title or f'<b>Книги</b>\nПо запросу: <b>{escape(session.query)}</b>'
 return f'{heading}\nПоказаны {start}-{end} из {total}. Страница {session.page+1}/{pages}.'
def search_results_keyboard(session:SearchSession):
 kb=InlineKeyboardBuilder()
 for item in page_items(session.results,session.page): kb.row(InlineKeyboardButton(text=(item.title if not item.author else f'{item.title} - {item.author}')[:64],callback_data=f'book:{item.book_id}'))
 pages=total_pages(len(session.results)); nav=[]
 if pages>1:
  if session.page>0: nav.append(InlineKeyboardButton(text='<< Назад',callback_data=f'page:{session.session_id}:{session.page-1}'))
  nav.append(InlineKeyboardButton(text=f'{session.page+1}/{pages}',callback_data='noop'))
  if session.page<pages-1: nav.append(InlineKeyboardButton(text='Еще >>',callback_data=f'page:{session.session_id}:{session.page+1}'))
 if nav: kb.row(*nav)
 return kb.as_markup()
def author_results_text(session:AuthorSession)->str:
 total=len(session.authors); pages=total_pages(total); start=session.page*SEARCH_PAGE_SIZE+1; end=min(total,(session.page+1)*SEARCH_PAGE_SIZE)
 return f'<b>Авторы</b>\nПо запросу: <b>{escape(session.query)}</b>\nПоказаны {start}-{end} из {total}. Страница {session.page+1}/{pages}.'
def author_results_keyboard(session:AuthorSession):
 kb=InlineKeyboardBuilder()
 for item in page_items(session.authors,session.page): kb.row(InlineKeyboardButton(text=item.name[:64],callback_data=f'author:{session.session_id}:{item.author_id}'))
 pages=total_pages(len(session.authors)); nav=[]
 if pages>1:
  if session.page>0: nav.append(InlineKeyboardButton(text='<< Назад',callback_data=f'apage:{session.session_id}:{session.page-1}'))
  nav.append(InlineKeyboardButton(text=f'{session.page+1}/{pages}',callback_data='noop'))
  if session.page<pages-1: nav.append(InlineKeyboardButton(text='Еще >>',callback_data=f'apage:{session.session_id}:{session.page+1}'))
 if nav: kb.row(*nav)
 return kb.as_markup()
def combined_results_text(query,books,authors):
 bl='\n'.join(f'• {escape(b.title)}'+(f' — {escape(b.author)}' if b.author else '') for b in books[:5]); al='\n'.join(f'• {escape(a.name)}' for a in authors[:5])
 return f'<b>Нашёл варианты</b>\nПо запросу: <b>{escape(query)}</b>\n\n<b>Книги</b>\n{bl}\n\n<b>Авторы</b>\n{al}'
def combined_results_keyboard(bs:SearchSession,aus:AuthorSession):
 kb=InlineKeyboardBuilder()
 for item in bs.results[:5]: kb.row(InlineKeyboardButton(text=(item.title if not item.author else f'{item.title} - {item.author}')[:64],callback_data=f'book:{item.book_id}'))
 for item in aus.authors[:5]: kb.row(InlineKeyboardButton(text=f'Автор: {item.name}'[:64],callback_data=f'author:{aus.session_id}:{item.author_id}'))
 kb.row(InlineKeyboardButton(text='Показать больше книг',callback_data=f'page:{bs.session_id}:0'),InlineKeyboardButton(text='Показать больше авторов',callback_data=f'apage:{aus.session_id}:0')); return kb.as_markup()
def book_text(details:BookDetails,annotation_max_chars:int,full_annotation:bool=False)->str:
 parts=[f'<b>{escape(details.title)}</b>']
 if details.authors: parts.append(escape(', '.join(details.authors[:5])))
 if details.translators: parts.append(f"Перевод: {escape(', '.join(details.translators[:5]))}")
 meta=[]
 if details.genres: meta.append(', '.join(details.genres[:3]))
 if details.file_size: meta.append(details.file_size)
 if details.pages: meta.append(f'{details.pages} с.')
 if meta: parts.append(escape(' · '.join(meta)))
 if details.annotation:
  text=details.annotation
  if not full_annotation and len(text)>annotation_max_chars: text=text[:annotation_max_chars-1].rstrip()+'…'
  parts.append(escape(text))
 if not details.formats: parts.append('Доступные форматы не найдены.')
 elif not any(i.code in {'epub','fb2','txt','mobi','pdf'} for i in details.formats): parts.append('Kindle-совместимый формат не найден.')
 return '\n\n'.join(parts)
def formats_keyboard(details:BookDetails,preferred_format:str|None,is_favorite:bool,annotation_max_chars:int):
 author_buttons=[i for i in details.author_refs[:3] if i.author_id]
 if not details.formats and not author_buttons:return None
 kb=InlineKeyboardBuilder()
 for item in author_buttons: kb.row(InlineKeyboardButton(text=f'Автор: {item.name[:48]}',callback_data=f'bauthor:{item.author_id}'))
 row=[]
 for item in sorted(details.formats,key=lambda i:i.code!=preferred_format):
  row.append(InlineKeyboardButton(text=(f'⭐ {item.label}' if item.code==preferred_format else item.label),callback_data=f'dl:{details.book_id}:{item.code}'))
  if len(row)==3: kb.row(*row); row=[]
 if row: kb.row(*row)
 kindle=next((c for c in [preferred_format,'epub','fb2','txt','mobi','pdf'] if c and any(f.code==c for f in details.formats)),None)
 if kindle: kb.row(InlineKeyboardButton(text=f'📤 Отправить {kindle.upper()} на Kindle',callback_data=f'kindle:{details.book_id}'))
 if details.annotation and len(details.annotation)>annotation_max_chars: kb.row(InlineKeyboardButton(text='Показать всю аннотацию',callback_data=f'annotation:{details.book_id}'))
 kb.row(InlineKeyboardButton(text='✅ В избранном' if is_favorite else '⭐ В избранное',callback_data=f"{'fav_remove' if is_favorite else 'fav_add'}:{details.book_id}")); return kb.as_markup()
def main_reply_keyboard():
 return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='⭐ Избранное'),KeyboardButton(text='🕘 История')],[KeyboardButton(text='📚 Последняя'),KeyboardButton(text='⚙️ Kindle')]],resize_keyboard=True,is_persistent=True,input_field_placeholder='Книга, автор или что хочется почитать')
def history_text(items:list[DownloadHistoryItem],failed:bool=False)->str:
 if not items:return '<b>Неудачные отправки</b>\n\nПока пусто.' if failed else '<b>История</b>\n\nПока пусто.'
 lines=['<b>Неудачные отправки</b>' if failed else '<b>История</b>']
 for item in items: lines.append(f"{item.created_at[:16]} — {item.title or item.book_id} [{item.format}] → {item.delivery_target}"+(f' ({item.error})' if failed and item.error else ''))
 return '\n'.join(lines)
