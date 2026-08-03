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
- отправка книг на Kindle и PocketBook по e-mail через generic SMTP/Gmail;


## Пользовательский интерфейс

Обычным пользователям бот не показывает slash-команды в Telegram menu. Основной сценарий простой: человек пишет название книги, автора или оба сразу, а частые действия открывает кнопками. Команды остаются скрытыми техническими fallback-ручками и продолжают работать, если набрать их вручную.

Главная нижняя клавиатура:
- ⭐ Избранное
- 🕘 История
- 📚 Последняя
- 📱 Читалки
- ❓ Помощь

Стартовый экран показывает примеры точного поиска и даёт inline-кнопки для поиска, читалок, избранного, истории и помощи. Админские команды скрыты от обычных пользователей; при необходимости их можно включить только для админских чатов через `UI_SHOW_ADMIN_COMMANDS=true`. Для отладки compact command menu можно вернуть флагом `UI_SHOW_POWER_USER_COMMANDS=true`, но production default — пустое меню команд.

## Надёжный поиск

Обычный текст обрабатывается детерминированно. Поиск книг и авторов выполняется параллельно, но весь запрос ограничен общим бюджетом `SEARCH_TOTAL_TIMEOUT_SECONDS`. Если точного результата нет, бот пробует не больше `SEARCH_FALLBACK_MAX_QUERIES` укороченных вариантов. Пустые ответы не кешируются надолго, а пользователь сразу видит сообщение о ходе поиска.

Рекомендуемые production-значения:

```env
SEARCH_TOTAL_TIMEOUT_SECONDS=12
SEARCH_FALLBACK_MAX_QUERIES=2
```

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

## Send to PocketBook

Open `📱 Читалки` → `PocketBook` and save the device address issued by Send-to-PocketBook. The address must end in `@pbsync.com`. PocketBook may ask the user to approve `SMTP_FROM_EMAIL` as a trusted sender after the first message.

PocketBook delivery reuses the same SMTP connection, in-process queue, rate limits and delivery history as Kindle. Preferred format order is EPUB, FB2, PDF, TXT. No additional service or dependency is required, and downloaded files are not stored after delivery.

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

Для разбора маршрутизации без сетевого запроса есть `/admin_intent <запрос>`: команда показывает решение intent-router и не обращается к Flibusta.

## Поиск: устройство и ограничения

Поиск полностью детерминированный — AI, Tavily и подборки удалены. Один `SearchService` обрабатывает название, автора и пару «название + автор», поэтому запрос не уходит в несколько конкурирующих веток и не создаёт дублирующиеся ответы.

- `исповедь толстой` и `толстой исповедь` ищутся регистронезависимо;
- случайная английская раскладка вроде `l.yf` получает один безопасный вариант `дуна`;
- книги и авторы запрашиваются параллельно, а частичный ответ сохраняется при сбое одной ветки;
- весь поиск ограничен `SEARCH_TOTAL_TIMEOUT_SECONDS`;
- число укороченных вариантов ограничено `SEARCH_FALLBACK_MAX_QUERIES`;
- свежий SQLite-кэш ускоряет повторные запросы, а устаревший кэш может спасти выдачу при временном сбое Flibusta;
- circuit breaker на короткое время прекращает бесполезные обращения после серии ошибок.

Жанровые и рекомендательные фразы не выдаются за точный поиск. Бот просит написать конкретное название или автора. Старые `AI_*`, `OPENAI_*`, `DISCOVERY_*`, `RECOMMENDATION_CONFIRMATION_*` и `LITERARY_*` переменные больше не используются; при плавном обновлении они безопасно игнорируются, но их следует удалить из `.env`.

Новые настройки надёжности:

```env
CACHE_STALE_IF_ERROR_SECONDS=604800
FLIBUSTA_CIRCUIT_BREAKER_FAILURES=3
FLIBUSTA_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30
```

### Operational troubleshooting

- Flibusta отвечает медленно: бот остановит запрос по общему таймауту и предложит повторить позже.
- Flibusta временно недоступна: при наличии используется недавний устаревший кэш.
- SMTP auth failed: проверь данные выбранного SMTP-провайдера; Gmail требует app password.
- Kindle mail missing: добавь `SMTP_FROM_EMAIL` в Amazon Personal Document settings.
- File too large: попробуй меньший формат; безопасный e-mail лимит по умолчанию — 28 МБ.
- Oracle подходит для хостинга: доставка уходит через настроенный SMTP.
