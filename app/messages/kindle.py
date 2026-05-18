from html import escape
from app.services.kindle import mask_email

def kindle_setup_text(sender: str | None) -> str:
 if not sender: return 'Отправка на Kindle пока не настроена владельцем бота.'
 return (
  '<b>Настройка Kindle</b>\n\n'
  '1. Найди свой Kindle e-mail в настройках Amazon.\n'
  f'2. Добавь <code>{escape(sender)}</code> в Approved Personal Document E-mail List.\n'
  '3. Сохрани адрес здесь: <code>/kindle_email your_name@kindle.com</code>\n'
  '4. После этого в карточке книги нажми «Отправить на Kindle».'
 )

def kindle_home_text(settings, sender: str | None) -> str:
 if settings is None:
  return '<b>Kindle не настроен</b>\n\nСначала сохрани свой Kindle e-mail, а затем добавь адрес отправителя в Amazon.'
 sender_text=escape(sender or 'не настроен владельцем бота')
 return (
  '<b>Kindle</b>\n\n'
  f'Адрес: {mask_email(settings.kindle_email)}\n'
  f'Формат: {escape(settings.preferred_kindle_format.upper())}\n'
  f'Разрешённый отправитель: <code>{sender_text}</code>'
 )

def kindle_missing_email_text() -> str:
 return 'Чтобы отправлять книги на Kindle, сначала настрой адрес.'
def kindle_sent_text(): return 'Отправлено на Kindle. Обычно книга появляется через несколько минут.'
def kindle_file_too_large_text(): return 'Файл слишком большой для отправки на Kindle по e-mail.'
def kindle_delivery_failed_text(): return 'Не удалось отправить книгу на Kindle. Попробуй позже.'
