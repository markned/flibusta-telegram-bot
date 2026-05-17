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

Status: the Kindle feature is an MVP with production-safety hardening: async SES SMTP delivery, SQLite-backed settings and delivery history, a lightweight in-process queue, user progress updates, rate limits, and operator health diagnostics.

The bot uses **Amazon SES through SMTP**. The bot itself can still run on Oracle Cloud or any VPS; **Oracle Email Delivery is not used** for this MVP.

### Required environment variables

```env
SMTP_PROVIDER=amazon_ses
SMTP_HOST=email-smtp.eu-central-1.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=books@my-domain.com
SMTP_STARTTLS=true
KINDLE_MAX_ATTACHMENT_MB=28
KINDLE_DEFAULT_FORMAT=epub
KINDLE_SEND_RATE_LIMIT_PER_HOUR=5
KINDLE_WORKER_CONCURRENCY=2
KINDLE_USER_CONCURRENCY=1
KINDLE_ENABLE_CONVERSION=false
KINDLE_CONVERSION_TARGET_FORMAT=epub
DATABASE_PATH=bot.db
ADMIN_USER_IDS=
```

`SMTP_HOST` must be the region-specific SES endpoint, such as `email-smtp.eu-central-1.amazonaws.com` or `email-smtp.us-east-1.amazonaws.com`. `SMTP_USERNAME` and `SMTP_PASSWORD` must be **SES SMTP credentials**, not regular AWS access keys. `KINDLE_MAX_ATTACHMENT_MB` defaults to `28` because MIME/base64 encoding inflates e-mail attachments.

### Amazon SES checklist

1. Verify a domain or sender e-mail identity in Amazon SES.
2. Enable DKIM.
3. Add the DNS records SES gives you.
4. Create SES SMTP credentials.
5. If the SES account is still in sandbox, request production access.
6. Use a stable sender address in `SMTP_FROM_EMAIL`, ideally something like `books@my-domain.com`.

If SES is still in sandbox, sending may work only to verified recipient addresses. That is an administrator setup issue, not a user issue.

### User Kindle setup

1. Find the Kindle e-mail in Amazon Kindle settings.
2. Add `SMTP_FROM_EMAIL` to **Amazon Approved Personal Document E-mail List**. Amazon rejects personal documents from unapproved senders, so this step is required.
3. Save the Kindle e-mail in the bot:

```text
/kindle_email my_name_123@kindle.com
```

Then use `📤 Send to Kindle` in a book card.

Kindle commands:
- `/kindle_email`
- `/kindle_help`
- `/kindle_status`
- `/kindle_remove`
- `/kindle_format`
- `/kindle_history`

Admin diagnostics:
- `/admin_kindle_health`

### Queue behavior and limitations

Kindle sending uses a lightweight in-process async queue. It keeps Telegram responsive and limits concurrent jobs globally and per user, but it is **not durable**: if the bot process restarts, queued jobs may be lost. SQLite delivery records are preserved, and interrupted in-flight jobs are marked failed on next startup.

### Future roadmap

- durable queue
- Calibre-backed conversion
- EPUB normalization
- stronger delivery retry system
