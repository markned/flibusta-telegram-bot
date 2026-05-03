# Flibusta Telegram Bot

Telegram-бот для поиска книг на Flibusta, просмотра описания и скачивания доступных форматов.

## Запуск

1. Создайте бота через BotFather и получите токен.
2. Подготовьте `.env`:

```bash
cp .env.example .env
```

3. Укажите `TELEGRAM_BOT_TOKEN` в `.env`. Если основной домен недоступен, замените `FLIBUSTA_BASE_URL`.
   Если контейнер не достучался до Telegram API, задайте `TELEGRAM_PROXY`.

   SOCKS5 proxy указывается так:

```env
TELEGRAM_PROXY=socks5://user:password@host:port
```

   Telegram использует `TELEGRAM_PROXY`. Flibusta использует `HTTP_PROXY`.
   Если нужен один и тот же proxy, укажите одинаковое значение в обеих переменных.
   Для нестабильного proxy можно поднять `FLIBUSTA_RETRIES` и `REQUEST_TIMEOUT_SECONDS`.
   Redirect при скачивании ограничивается `FLIBUSTA_MAX_REDIRECTS`.
   Подробные технические логи скачивания включаются через `LOG_LEVEL=DEBUG`.
4. Запустите:

```bash
docker compose up --build
```

## Команды

- `/start` - краткая справка.
- `/search <текст>` - поиск книг.
- `/author <имя>` - поиск автора и выбор книги из его списка.
- Любой текст без команды тоже запускает поиск.

В результатах можно открыть карточку книги, посмотреть описание и скачать доступный формат.
