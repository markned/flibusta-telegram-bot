# Flibusta Telegram Bot

Лёгкий Telegram-бот для поиска и скачивания книг с Flibusta.

## Что умеет
- умный поиск: обычный ввод сам пытается понять, книга это или автор;
- отдельные режимы поиска книги и автора;
- карточка книги и выбор формата;
- постоянное меню в Telegram;
- запоминание предпочитаемого формата пользователя без базы данных.
- отправка книг на Kindle по e-mail через Amazon SES SMTP.

## Локальный запуск
```bash
cp .env.example .env
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.main
```

## Деплой
См. `deploy/oracle-cloud.md`.

## Send to Kindle

Бот может отправлять выбранную книгу вложением на Kindle e-mail пользователя. Для MVP используется **Amazon SES через SMTP**: сам бот всё ещё можно хостить на Oracle Cloud или любом VPS, но Oracle Email Delivery здесь не используется.

### Переменные окружения

```env
SMTP_PROVIDER=amazon_ses
SMTP_HOST=email-smtp.eu-central-1.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=books@my-domain.com
SMTP_STARTTLS=true
KINDLE_MAX_ATTACHMENT_MB=28
DATABASE_PATH=bot.db
```

`SMTP_HOST` должен быть региональным SES endpoint, например `email-smtp.eu-central-1.amazonaws.com` или `email-smtp.us-east-1.amazonaws.com`. `SMTP_USERNAME` и `SMTP_PASSWORD` — это именно **SES SMTP credentials**, не обычные AWS access key / secret key. `KINDLE_MAX_ATTACHMENT_MB` по умолчанию равен `28`, потому что MIME/base64 увеличивает размер вложения.

### Настройка Amazon SES

1. Подтвердить домен или адрес отправителя в Amazon SES.
2. Включить DKIM.
3. Добавить DNS-записи, которые выдаст SES.
4. Создать SES SMTP credentials.
5. Если аккаунт SES ещё в sandbox, запросить production access.
6. Использовать стабильный адрес отправителя в `SMTP_FROM_EMAIL`, например `books@my-domain.com`.

Если SES всё ещё в sandbox, отправка может работать только на подтверждённые адреса получателей. Это административная настройка SES, а не проблема пользователя.

### Настройка пользователем

1. Найти Kindle e-mail в настройках Amazon Kindle.
2. Добавить `SMTP_FROM_EMAIL` в **Amazon Approved Personal Document E-mail List**.
3. Сохранить Kindle e-mail в боте:

```text
/kindle_email my_name_123@kindle.com
```

После этого в карточке книги появится рабочая кнопка `📤 Send to Kindle`.

Доступные команды:
- `/kindle_email`
- `/kindle_help`
- `/kindle_status`
- `/kindle_remove`
