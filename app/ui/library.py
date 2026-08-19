from __future__ import annotations
from datetime import UTC, datetime, timedelta
from html import escape
from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.flibusta import BookDetails
from app.pagination import SEARCH_PAGE_SIZE, page_items, total_pages
from app.repositories.download_history import DownloadHistoryItem
from app.state import SearchSession, AuthorSession

def search_results_text(session:SearchSession,title:str|None=None)->str:
 total=len(session.results); pages=total_pages(total); start=session.page*SEARCH_PAGE_SIZE+1; end=min(total,(session.page+1)*SEARCH_PAGE_SIZE)
 heading=title or session.title or f'<b>Нашёл книги</b>\nЗапрос: <b>{escape(session.query)}</b>'
 page_text=f'\nСтраница {session.page+1}/{pages}' if pages>1 else ''
 return f'{heading}\n\nПоказаны {start}–{end} из {total}{page_text}'
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
 page_text=f'\nСтраница {session.page+1}/{pages}' if pages>1 else ''
 return f'<b>Нашёл авторов</b>\nЗапрос: <b>{escape(session.query)}</b>\n\nПоказаны {start}–{end} из {total}{page_text}'
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
 return f'<b>Нашёл несколько вариантов</b>\nЗапрос: <b>{escape(query)}</b>\n\nСначала книги, ниже авторы.\n\n<b>Книги</b>\n{bl}\n\n<b>Авторы</b>\n{al}'
def combined_results_keyboard(bs:SearchSession,aus:AuthorSession):
 kb=InlineKeyboardBuilder()
 for item in bs.results[:5]: kb.row(InlineKeyboardButton(text=(item.title if not item.author else f'{item.title} - {item.author}')[:64],callback_data=f'book:{item.book_id}'))
 for item in aus.authors[:5]: kb.row(InlineKeyboardButton(text=f'Автор: {item.name}'[:64],callback_data=f'author:{aus.session_id}:{item.author_id}'))
 kb.row(InlineKeyboardButton(text='Показать больше книг',callback_data=f'page:{bs.session_id}:0'),InlineKeyboardButton(text='Показать больше авторов',callback_data=f'apage:{aus.session_id}:0')); return kb.as_markup()
def book_text(details:BookDetails,annotation_max_chars:int,full_annotation:bool=False)->str:
 parts=[f'<b>{escape(details.title)}</b>']
 if details.authors: parts.append(escape(', '.join(details.authors[:5])))
 if details.series:
  series_bits=[]
  for item in details.series[:2]:
   label=item.name + (f' #{item.position}' if item.position else '')
   series_bits.append(label)
  if series_bits: parts.append('Серия: '+escape(', '.join(series_bits)))
 if details.translators: parts.append(f"Перевод: {escape(', '.join(details.translators[:5]))}")
 meta=[]
 if details.genres: meta.append(', '.join(details.genres[:5]))
 if details.file_size: meta.append(details.file_size)
 if details.pages: meta.append(f'{details.pages} с.')
 if meta: parts.append(escape(' · '.join(meta)))
 if details.annotation:
  text=' '.join(details.annotation.split())
  if not full_annotation and len(text)>annotation_max_chars: text=text[:annotation_max_chars-1].rstrip()+'…'
  parts.append(escape(text))
 if not details.formats: parts.append('Доступные форматы не найдены.')
 elif not any(i.code in {'epub','fb2','txt','mobi','pdf'} for i in details.formats): parts.append('Подходящий для читалки формат не найден.')
 return '\n\n'.join(parts)
def formats_keyboard(details:BookDetails,preferred_format:str|None,is_favorite:bool,annotation_max_chars:int):
 author_buttons=[i for i in details.author_refs[:2] if i.author_id]
 if not details.formats and not author_buttons:return None
 kb=InlineKeyboardBuilder()
 reader_format=next((c for c in [preferred_format,'epub','fb2','txt','mobi','pdf'] if c and any(f.code==c for f in details.formats)),None)
 if reader_format: kb.row(InlineKeyboardButton(text='📤 На читалку',callback_data=f'reader_send:{details.book_id}'))
 ordered=sorted(details.formats,key=lambda i:(i.code!=preferred_format, i.code not in {'epub','fb2','txt','pdf','mobi'}, i.code))
 row=[]
 for item in ordered:
  label=f'⬇️ {item.code.upper()}' if item.code==preferred_format else item.code.upper()
  row.append(InlineKeyboardButton(text=label,callback_data=f'dl:{details.book_id}:{item.code}'))
  if len(row)==3: kb.row(*row); row=[]
 if row: kb.row(*row)
 kb.row(InlineKeyboardButton(text='✅ В избранном' if is_favorite else '⭐ В избранное',callback_data=f"{'fav_remove' if is_favorite else 'fav_add'}:{details.book_id}"))
 for item in author_buttons: kb.row(InlineKeyboardButton(text=f'👤 {item.name[:48]}',callback_data=f'bauthor:{item.author_id}'))
 for item in details.series[:2]:
  if item.series_id: kb.row(InlineKeyboardButton(text=f'📚 {item.name[:48]}',callback_data=f'series:{item.series_id}'))
 if details.annotation and len(details.annotation)>annotation_max_chars: kb.row(InlineKeyboardButton(text='📖 Описание полностью',callback_data=f'annotation:{details.book_id}'))
 return kb.as_markup()
def main_reply_keyboard():
 return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='⭐ Избранное'),KeyboardButton(text='🕘 История')],[KeyboardButton(text='📚 Последняя'),KeyboardButton(text='📱 Мои устройства')],[KeyboardButton(text='❓ Помощь')]],resize_keyboard=True,is_persistent=True,input_field_placeholder='Название книги или автор')
def history_text(
    items: list[DownloadHistoryItem],
    failed: bool = False,
    *,
    now: datetime | None = None,
) -> str:
    heading = "<b>Неудачные отправки</b>" if failed else "<b>История книг</b>"
    if not items:
        suffix = "Здесь появятся книги, которые не удалось отправить." if failed else "Здесь появятся скачанные и отправленные книги."
        return f"{heading}\n\nПока пусто. {suffix}"

    lines = [
        heading,
        "Последние неудачные попытки:" if failed else "Последние скачанные и отправленные книги:",
    ]
    for index, item in enumerate(items, start=1):
        title = escape(item.title or "Книга без названия")
        author = escape(item.author or "Автор не указан")
        when = _friendly_history_date(item.created_at, now=now)
        action = _history_action(item.delivery_target, failed=failed)
        details = f"{when} · {escape(item.format.upper())} · {action}"
        lines.append(f"<b>{index}. {title}</b>\n{author}\n{details}")
        if failed:
            lines.append(f"Причина: {_friendly_history_error(item.error)}")
    lines.append("Нажми название ниже, чтобы снова открыть карточку книги.")
    return "\n\n".join(lines)


def history_keyboard(items: list[DownloadHistoryItem], failed: bool = False):
    kb = InlineKeyboardBuilder()
    seen: set[str] = set()
    for item in items:
        if not item.book_id or item.book_id in seen:
            continue
        seen.add(item.book_id)
        label = f"📖 {item.title or 'Открыть книгу'}"
        kb.row(
            InlineKeyboardButton(
                text=label[:64],
                callback_data=f"book:{item.book_id}",
            )
        )
    if failed:
        kb.row(InlineKeyboardButton(text="✅ Успешные", callback_data="home_history"))
    else:
        kb.row(InlineKeyboardButton(text="⚠️ Неудачные", callback_data="history_failed"))
    kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="home"))
    return kb.as_markup()


def _friendly_history_date(value: str, *, now: datetime | None = None) -> str:
    try:
        created = datetime.fromisoformat(value)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return "Дата неизвестна"
    current = now or datetime.now(UTC)
    current = current.astimezone(created.tzinfo)
    if created.date() == current.date():
        day = "Сегодня"
    elif created.date() == (current - timedelta(days=1)).date():
        day = "Вчера"
    else:
        day = created.strftime("%d.%m.%Y")
    return f"{day}, {created.strftime('%H:%M')}"


def _history_action(target: str, *, failed: bool) -> str:
    labels = {
        "telegram": "Telegram",
        "kindle": "Kindle",
        "pocketbook": "PocketBook",
    }
    destination = labels.get((target or "").casefold(), "читалка")
    verb = "не отправлено в" if failed else "в"
    return f"{verb} {destination}"


def _friendly_history_error(error: str | None) -> str:
    normalized = (error or "").casefold()
    if "too_large" in normalized or "too large" in normalized or "слишком" in normalized:
        return "файл оказался слишком большим"
    if "recipient" in normalized or "sender" in normalized or "address" in normalized:
        return "проверь адрес читалки и разрешённого отправителя"
    if "auth" in normalized or "config" in normalized:
        return "сервис отправки временно недоступен"
    if "telegram upload" in normalized:
        return "Telegram не принял файл"
    return "не удалось завершить отправку"
