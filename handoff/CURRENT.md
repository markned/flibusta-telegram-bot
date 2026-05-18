# Current handoff

## Project shape
- aiogram Telegram bot
- SQLite persistence and migrations
- optional AI recommendation layer
- Kindle delivery through SES SMTP

## Current guardrails
- low-memory VPS target
- exact search must not depend on AI
- no permanent storage of downloaded files
- no real network calls in tests

## Before taking work
1. Read `AGENTS.md` and `.codex/instructions.md`.
2. Pick the narrowest agent/skill.
3. Run the relevant narrow tests before broad edits.

## Before handing off
- run `make check`
- note any intentionally deferred work here
