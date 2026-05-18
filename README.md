# Flibusta Telegram Bot

Лёгкий Telegram-бот для поиска и скачивания книг с Flibusta.

## Что умеет
- закрытый вход: по приглашению или после одобрения админом;
- умный поиск: обычный ввод сам пытается понять, книга это или автор;
- любой обычный текст сразу проходит через умный поиск без выбора режима;
- карточка книги и выбор формата;
- постоянное меню в Telegram;
- SQLite-кэш для поиска и карточек;
- избранное, история отправок и команда `/last`;
- более осторожный smart search для неоднозначных запросов;
- запоминание предпочитаемого формата пользователя в SQLite;
- отправка книг на Kindle по e-mail через Amazon SES SMTP;
- опциональный AI-помощник для формулировки книжных запросов.

## Локальный запуск
```bash
cp .env.example .env
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.main
```

## Структура кода

- `app/main.py` — сборка зависимостей, bootstrap и тонкая маршрутизация;
- `app/ui/` — рендеринг пользовательских экранов и клавиатур;
- `app/state.py` — короткоживущие сессии выдач;
- `app/services/search_logic.py` — чистая логика нормализации и ранжирования поиска;
- `app/handlers/` — крупные пользовательские контуры вроде Kindle и админки.

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
CACHE_ENABLED=true
CACHE_BOOK_SEARCH_TTL_SECONDS=1800
CACHE_AUTHOR_SEARCH_TTL_SECONDS=1800
CACHE_SMART_SEARCH_TTL_SECONDS=1800
CACHE_BOOK_DETAILS_TTL_SECONDS=21600
CACHE_AUTHOR_BOOKS_TTL_SECONDS=21600
BOOK_ANNOTATION_MAX_CHARS=1200
SEARCH_RATE_LIMIT_PER_MINUTE=20
DOWNLOAD_RATE_LIMIT_PER_HOUR=30
ACCESS_CONTROL_ENABLED=true
AI_ENABLED=false
OPENAI_API_KEY=
AI_MODEL=gpt-5-nano
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
- `/kindle`
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

### Production notes
- SQLite lives at `DATABASE_PATH`; startup runs small idempotent migrations automatically.
- Legacy `user_prefs.json` is imported once and renamed to `user_prefs.json.migrated`.
- Useful admin commands: `/admin_kindle_health`, `/admin_kindle_failures`, `/admin_kindle_delivery <id>`, `/admin_export_settings`, `/admin_cleanup_deliveries`.
- Before deployment, verify SES identity, DKIM, production access, SMTP credentials, `SMTP_FROM_EMAIL`, and the SQLite path is writable.

## Product features

- `/favorites` or `/fav` — saved books; only metadata is stored, never the downloaded book files.
- `/history` and `/history_failed` — recent Telegram/Kindle sends and failures.
- `/last` — reopen the last book with quick actions.
- Smart search strips format hints like `epub`, recognizes quoted titles and author-looking queries, and shows books plus authors together when the query is ambiguous.
- Flibusta search/details responses are cached in SQLite with short TTLs to make repeated requests faster and gentler on the source site.
- Long annotations are shortened in cards; the full text is available by button.
- Series support is scaffolded in the data model, but the button stays hidden until Flibusta exposes reliable data for a book.

Admin product commands:
- `/admin` — компактная панель управления;
- `/admin_user_add <id>` / `/admin_user_remove <id>`;
- `/admin_stats`
- `/admin_cache_stats`
- `/admin_cache_clear`
- `/admin_cache_clear all`
- `/invite` — создать invite-link; `/invite 5` даст пять активаций.

Rate limits are intentionally small and boring: search is limited per minute in memory, Telegram downloads per hour via SQLite history, admins bypass both.

## Access control

При `ACCESS_CONTROL_ENABLED=true` новый пользователь не попадает в библиотеку сразу: `/start` создаёт запрос, а админ получает кнопки «Разрешить / Отклонить». Для доверенных людей можно создать deep-link командой `/invite`; состояние хранится в SQLite.

В `/admin` есть быстрый обзор, заявки, список пользователей, блокировка/удаление, инвайты, статистика и очистка просроченного кэша.

## AI assistant

AI-рекомендации — best-effort enhancement поверх обычного поиска. Точные запросы сначала обрабатываются детерминированно; если AI недоступен или даёт слабый план, бот сохраняет обычный поиск. По умолчанию AI выключен; для включения нужны `AI_ENABLED=true` и `OPENAI_API_KEY`.

Настройки recommendation budget: `AI_INTENT_CACHE_TTL_SECONDS`, `AI_RECOMMENDATION_MAX_QUERIES_USED`, `AI_RECOMMENDATION_TARGET_RESULTS`, `AI_RECOMMENDATION_MIN_RESULTS`, `AI_RECOMMENDATION_MAX_DETAILS`, `AI_RECOMMENDATION_BOOKS_PER_QUERY`.

### Troubleshooting
- Domain verified but mail is not delivered: check DKIM and SES event history.
- DKIM pending: wait for DNS propagation and verify records.
- SES sandbox: recipients may also need verification until production access is approved.
- SMTP auth failed: use SES SMTP credentials, not AWS access keys.
- Kindle mail missing: approve `SMTP_FROM_EMAIL` in Amazon Personal Document settings.
- File too large: try a smaller format; the default e-mail-safe limit is 28 MB.
- Hosting on Oracle is fine: delivery still leaves through Amazon SES SMTP.

## Web discovery with Tavily

`/recommend` делает дешёвую локальную/модельную подборку. `/discover` может добавить Tavily, если включены `DISCOVERY_USE_WEB=true`, `DISCOVERY_WEB_PROVIDER=tavily` и ключ передан только через `DISCOVERY_WEB_API_KEY`; `/discover_web` явно просит веб-подборку. Финальный список всё равно проходит через Flibusta: бот показывает только книги с реальным `book_id` из каталога.

Веб-поиск кэшируется и ограничен дневными лимитами, а консервативные caps держат нагрузку подходящей для маленького VPS. `.env` не коммитится; ключ Tavily нельзя добавлять в код, README или логи.

Главные настройки: `DISCOVERY_ENABLED`, `DISCOVERY_USE_WEB`, `DISCOVERY_WEB_PROVIDER`, `DISCOVERY_WEB_API_KEY`, `DISCOVERY_MAX_WEB_RESULTS`, `DISCOVERY_MAX_BOOK_IDEAS`, `DISCOVERY_MAX_FLIBUSTA_CHECKS`, `DISCOVERY_MAX_FINAL_RESULTS`, `DISCOVERY_CACHE_TTL_SECONDS`, `DISCOVERY_USER_DAILY_LIMIT`, `DISCOVERY_GLOBAL_DAILY_LIMIT`.
