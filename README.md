# Flibusta Telegram Bot

Лёгкий Telegram-бот для поиска и скачивания книг с Flibusta.

## Что умеет
- умный поиск: обычный ввод сам пытается понять, книга это или автор;
- отдельные режимы поиска книги и автора;
- карточка книги и выбор формата;
- постоянное меню в Telegram;
- запоминание предпочитаемого формата пользователя без базы данных.

## Локальный запуск
```bash
cp .env.example .env
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.main
```

## Деплой
См. `deploy/oracle-cloud.md`.
