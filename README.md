# Flibusta Telegram Bot

Лёгкий Telegram-бот для поиска и скачивания книг с Flibusta.

## Что умеет
- закрытый вход: по приглашению или после одобрения админом;
- умный поиск: обычный ввод сам пытается понять, книга это или автор;
- любой обычный текст сразу проходит через умный поиск без выбора режима;
- карточка книги и выбор формата;
- чистый Telegram UI: обычный текст, нижняя клавиатура и inline-кнопки;
- SQLite-кэш для поиска и карточек;
- избранное, история отправок и последняя книга через кнопки;
- более осторожный smart search для неоднозначных запросов;
- запоминание предпочитаемого формата пользователя в SQLite;
- отправка книг на Kindle по e-mail через generic SMTP/Gmail;
- опциональный AI-помощник для формулировки книжных запросов.


## Пользовательский интерфейс

Обычным пользователям бот не показывает slash-команды в Telegram menu. Основной сценарий простой: человек пишет название, автора или настроение, а частые действия открывает кнопками. Команды остаются скрытыми техническими fallback-ручками и продолжают работать, если набрать их вручную.

Главная нижняя клавиатура:
- ⭐ Избранное
- 🕘 История
- 📚 Последняя
- ⚙️ Kindle
- ❓ Помощь

Стартовый экран объясняет примеры запросов и даёт inline-кнопки для поиска, Kindle, избранного, истории и помощи. Админские команды скрыты от обычных пользователей; при необходимости их можно включить только для админских чатов через `UI_SHOW_ADMIN_COMMANDS=true`. Для отладки compact command menu можно вернуть флагом `UI_SHOW_POWER_USER_COMMANDS=true`, но production default — пустое меню команд.

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

Kindle delivery is implemented as generic SMTP: the bot downloads the selected book, attaches it to an e-mail, and sends it to the user’s Kindle address. Gmail SMTP is the practical default for a private/family bot; Amazon SES remains supported as an optional SMTP provider, but it is not required.

The Kindle contour is intentionally small: SQLite settings and delivery history, a lightweight in-process queue, user progress updates, rate limits, and operator diagnostics. No book files are stored permanently.

### Required environment variables

Start from `.env.gmail.example` for Gmail or `.env.production.example` for the full production template. Do not commit `.env`.

```env
SMTP_PROVIDER=gmail
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your.dedicated.gmail@gmail.com
SMTP_PASSWORD=your-google-app-password
SMTP_FROM_EMAIL=your.dedicated.gmail@gmail.com
SMTP_STARTTLS=true
SMTP_CUSTOM_DOMAIN=
SMTP_DNS_CHECKS_ENABLED=false
KINDLE_MAX_ATTACHMENT_MB=28
KINDLE_DEFAULT_FORMAT=epub
KINDLE_SEND_RATE_LIMIT_PER_HOUR=5
KINDLE_WORKER_CONCURRENCY=1
KINDLE_USER_CONCURRENCY=1
DATABASE_PATH=bot.db
ADMIN_USER_IDS=
```

Supported `SMTP_PROVIDER` values: `custom`, `gmail`, `google_workspace`, `zoho`, `brevo`, `mailgun`, `amazon_ses`, `disabled`. Presets fill safe host/STARTTLS defaults where possible; explicit `SMTP_HOST` still wins.

For Gmail, enable 2-Step Verification and create a Google app password. `SMTP_PASSWORD` must be that app password, not the normal mailbox password. `KINDLE_MAX_ATTACHMENT_MB` defaults to `28` because MIME/base64 encoding inflates e-mail attachments.

### User Kindle setup

The UI is button-first: open `⚙️ Kindle` and follow the buttons. Slash commands still work for maintenance.

1. Find the Kindle e-mail in Amazon Kindle settings.
2. Add `SMTP_FROM_EMAIL` to **Amazon Approved Personal Document E-mail List**. Amazon rejects personal documents from unapproved senders, so this step is required.
3. Save the Kindle e-mail in the bot.
4. Optionally press “Отправить тест”.
5. Use `📤 Kindle EPUB` or `📤 На Kindle` in a book card.

Kindle is button-first. Open `⚙️ Kindle`, then use the buttons to save the Kindle address, show the sender, change format, send a test, view history, or delete the address. Hidden Kindle slash commands still exist for maintenance, but they are intentionally not shown in Telegram's command menu.

Admin diagnostics remain available manually and can be exposed only to admins with scoped command menus if `UI_SHOW_ADMIN_COMMANDS=true`.

### Queue behavior and limitations

Kindle sending uses a lightweight in-process async queue. It keeps Telegram responsive and limits concurrent jobs globally and per user, but it is **not durable**: if the bot process restarts, queued jobs may be lost. SQLite delivery records are preserved, and interrupted in-flight jobs are marked failed on next startup.

### Production notes

- SQLite lives at `DATABASE_PATH`; startup runs small idempotent migrations automatically.
- Legacy `user_prefs.json` is imported once and renamed to `user_prefs.json.migrated`.
- The `approved_sender_confirmed` flag records whether the user has said they added the bot sender to Amazon.
- Admin commands remain hidden by default. Type them manually or enable a small admin-only command menu with `UI_SHOW_ADMIN_COMMANDS=true`.
- Gmail private-use deployments should keep `KINDLE_WORKER_CONCURRENCY=1`.

### Kindle troubleshooting

- SMTP auth failed: for Gmail, use a Google app password and check 2-Step Verification.
- Kindle mail missing: approve `SMTP_FROM_EMAIL` in Amazon Personal Document settings.
- Sender rejected: make sure `SMTP_FROM_EMAIL` matches the SMTP account/provider rules.
- File too large: try a smaller format; the default e-mail-safe limit is 28 MB.
- Hosting on Oracle is fine: delivery leaves through the configured SMTP provider.


## Book covers and Kindle EPUB metadata

Book cards can show a cover photo when a reliable cover is found. The lookup is best-effort and cached in SQLite by URL metadata only; image bytes are not cached or prefetched. If lookup, download, or Telegram photo sending fails, the bot falls back to the normal text card. Wrong covers are worse than no covers, so low-confidence candidates are rejected.

For Kindle, EPUB files can be lightly polished before sending: the bot tries to hard-set title/author metadata and embed the best reliable cover using Calibre `ebook-meta` only. This is optional: if Calibre is missing or `ebook-meta` fails, the original EPUB is sent. The bot does not use `ebook-convert` and does not convert FB2 to EPUB in this phase.

Optional server dependency:

```bash
sudo apt update && sudo apt install -y calibre
```

Relevant env vars: `BOOK_COVER_UI_ENABLED`, `COVER_LOOKUP_ENABLED`, `COVER_PROVIDER_ORDER`, `COVER_MAX_DOWNLOAD_MB`, `COVER_MIN_CONFIDENCE`, `GOOGLE_BOOKS_API_KEY`, `KINDLE_METADATA_POLISH_ENABLED`, `KINDLE_METADATA_TOOL`, `KINDLE_EMBED_COVER_ENABLED`, `KINDLE_FILENAME_TEMPLATE`. Keep `KINDLE_WORKER_CONCURRENCY=1` for small VPS/Gmail deployments.

## Product features

- Favorites, history, and the last opened book are available through buttons; only metadata is stored, never downloaded book files.
- Hidden manual commands still exist for maintenance and debugging, but are not part of the normal UI.
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
Для разбора маршрутизации без поиска есть `/admin_intent <запрос>`: команда показывает решение intent-router, но не вызывает AI, Tavily или Flibusta. Для неоднозначных фраз правило простое: AI/discovery лишь расширяют подборку, а детерминированный поиск остаётся запасным контуром.

## AI assistant

AI-рекомендации — best-effort enhancement поверх обычного поиска. Точные запросы сначала обрабатываются детерминированно; если AI недоступен или даёт слабый план, бот сохраняет обычный поиск. По умолчанию AI выключен; для включения нужны `AI_ENABLED=true` и `OPENAI_API_KEY`.

Настройки recommendation budget: `AI_INTENT_CACHE_TTL_SECONDS`, `AI_RECOMMENDATION_MAX_QUERIES_USED`, `AI_RECOMMENDATION_TARGET_RESULTS`, `AI_RECOMMENDATION_MIN_RESULTS`, `AI_RECOMMENDATION_MAX_DETAILS`, `AI_RECOMMENDATION_BOOKS_PER_QUERY`.

### Operational troubleshooting
- AI disabled: exact search, author search, downloads, Kindle, favorites, and history still work.
- Web discovery disabled: broad recommendations fall back to model/local search and then deterministic search.
- SMTP auth failed: check the selected SMTP provider credentials; Gmail requires an app password.
- Kindle mail missing: approve `SMTP_FROM_EMAIL` in Amazon Personal Document settings.
- File too large: try a smaller format; the default e-mail-safe limit is 28 MB.
- Hosting on Oracle is fine: delivery leaves through the configured SMTP provider.

## Web discovery with Tavily

`/recommend` делает дешёвую локальную/модельную подборку. `/discover` может добавить Tavily, если включены `DISCOVERY_USE_WEB=true`, `DISCOVERY_WEB_PROVIDER=tavily` и ключ передан только через `DISCOVERY_WEB_API_KEY`; `/discover_web` явно просит веб-подборку. Финальный список всё равно проходит через Flibusta: бот показывает только книги с реальным `book_id` из каталога.

Веб-поиск кэшируется и ограничен дневными лимитами, а консервативные caps держат нагрузку подходящей для маленького VPS. `.env` не коммитится; ключ Tavily нельзя добавлять в код, README или логи.
Точные названия, авторы и пары «автор + название» не вызывают Tavily вовсе.

Главные настройки: `DISCOVERY_ENABLED`, `DISCOVERY_USE_WEB`, `DISCOVERY_WEB_PROVIDER`, `DISCOVERY_WEB_API_KEY`, `DISCOVERY_MAX_WEB_RESULTS`, `DISCOVERY_MAX_BOOK_IDEAS`, `DISCOVERY_MAX_FLIBUSTA_CHECKS`, `DISCOVERY_MAX_FINAL_RESULTS`, `DISCOVERY_CACHE_TTL_SECONDS`, `DISCOVERY_USER_DAILY_LIMIT`, `DISCOVERY_GLOBAL_DAILY_LIMIT`.

### Recommendation confirmation

Свободный текст по-прежнему маршрутизируется автоматически. Точный поиск и запросы «автор + название» выполняются сразу; широкие просьбы о подборке сначала получают короткое подтверждение, и только после него запускают model/web discovery. Это удерживает дорогие запросы под контролем и не заставляет пользователя выбирать режим вручную. Темы для взрослых обрабатываются как обычный поиск книг, без лишних предупреждений и без превращения ответа в практическую инструкцию.

Поиск пар «название + автор» регистронезависимый: `исповедь толстой` и `толстой исповедь` обрабатываются так же, как версии с заглавными буквами. Для будущих легальных книжных каталогов оставлен отключённый provider abstraction; сейчас используются Tavily discovery и проверка наличия в Flibusta.
