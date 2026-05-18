# Current handoff

## Project shape
- aiogram Telegram bot
- SQLite persistence and migrations
- optional AI recommendation layer
- Kindle delivery through SES SMTP

## Current task plan
Files likely to change:
- `app/main.py`
- `app/services/query_analyzer.py`
- `app/services/ai_assistant.py`
- `app/services/recommendation_packs.py`
- `app/services/recommendation_filters.py`
- `app/config.py`, `.env.example`
- focused tests and small README note

Tests to add/update:
- `/search` sends a response again
- routing: exact vs recommendation-like text
- reversed author/title query such as `Исповедь Толстой`
- recommendation detection and curated packs
- bad recommendation candidate filtering
- AI failure / weak recommendation fallback
- details cap and Kindle button response

Expected behavior:
- deterministic search works without AI
- recommendations use bounded AI + deterministic packs
- weak AI results fall back before failure copy is shown
- low-memory limits stay explicit and capped

Risks:
- recommendation fallback can accidentally intercept exact search
- author/title heuristics can over-match short phrases
- too much expansion can increase Flibusta fan-out if not capped

## Current guardrails
- low-memory VPS target
- exact search must not depend on AI
- no permanent storage of downloaded files
- no real network calls in tests

## Before handing off
- `make test-search` ✅
- `make check` ✅

## Completed in this pass
- restored result sending in `/search`
- kept free-text routing deterministic unless the query is recommendation-like
- retained reverse title/surname fallback for cases like `Исповедь Толстой`
- fixed recommendation regex matching and added bounded books-per-query config
- moved bad-title filtering into a dedicated helper
- expanded curated recommendation packs and fallback use
- ensured exhausted AI result paths still try smart search before no-results

## Intentionally deferred
- no broader ranking redesign; the current pass stays deliberately small and bounded
