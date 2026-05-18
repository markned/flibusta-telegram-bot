# AGENTS.md

This repo is a small aiogram + SQLite Telegram bot. Keep changes boring, modular, and cheap to operate.

## Hard constraints
- Target is a low-memory VPS.
- SQLite only. Do **not** add Redis, Celery, RabbitMQ, Postgres, vector DBs, or external search services.
- AI is an optional enhancement, never a dependency for exact search.
- If AI fails, deterministic search must still work.
- Final book recommendations must point to real Flibusta `book_id` values.
- Do not store downloaded book files permanently.
- Do not expose secrets in logs, docs, tests, or examples.
- Tests must not make real network calls.

## Working agreement
1. Read `handoff/CURRENT.md` and `.codex/instructions.md` first.
2. Prefer the narrowest agent/skill that fits the task.
3. Keep write scopes separate when multiple agents work in parallel.
4. Preserve existing search, download, Kindle, favorites, and history flows.
5. Before handoff, run `make check` and update `handoff/CURRENT.md` if the task materially changes repo state.
