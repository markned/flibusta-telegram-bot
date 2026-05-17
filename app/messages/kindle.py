def kindle_setup_text(sender):
 if not sender: return 'Отправка на Kindle пока не настроена владельцем бота.'
 return f'1. Найдите Kindle e-mail в Amazon.\n2. Добавьте {sender} в Approved Personal Document E-mail List.\n3. Сохраните адрес: /kindle_email your_name@kindle.com\n4. Проверьте отправкой небольшого EPUB.'
def kindle_missing_email_text(): return 'Kindle e-mail пока не настроен. Используйте /kindle_email your_name@kindle.com'
def kindle_sent_text(): return 'Отправлено на Kindle. Обычно книга появляется через несколько минут.'
def kindle_file_too_large_text(): return 'Файл слишком большой для отправки на Kindle по e-mail.'
def kindle_delivery_failed_text(): return 'Не удалось отправить книгу на Kindle. Попробуйте позже.'
