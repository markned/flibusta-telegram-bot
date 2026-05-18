# Current handoff

## Project shape
- aiogram Telegram bot
- SQLite persistence and migrations
- optional AI recommendation layer
- Kindle delivery through SES SMTP

## Current task plan
Files likely to change:
- `app/main.py`
- `app/services/intent_router.py`
- `app/services/ai_assistant.py`
- `app/services/recommendation_filters.py`
- focused routing tests

Tests to add/update:
- recommendation instructions route automatically
- exact title `Подборка стихотворений` remains exact
- author and author-title examples route deterministically
- weak recommendation anchors are rejected
- exact title path never calls AI/discovery

Expected behavior:
- free text auto-routes without requiring slash commands
- instruction word `подборка` never becomes a recommendation search anchor
- exact titles and author searches remain web-free and deterministic

Risks:
- cheap heuristics can misclassify uncommon two-word titles
- recommendation topic stripping must stay conservative

## Current guardrails
- low-memory VPS target
- exact search must not depend on AI
- no permanent storage of downloaded files
- no real network calls in tests

## Before handing off
- `make check` ✅

## Completed in previous pass
- restored result sending in `/search`
- kept free-text routing deterministic unless the query is recommendation-like
- retained reverse title/surname fallback for cases like `Исповедь Толстой`
- fixed recommendation regex matching and added bounded books-per-query config

## Completed in this pass
- added a bounded discovery service layer with Tavily provider, idea generator, matcher, and recommender
- added `/discover`, `/discover_web`, and discovery-backed `/recommend`
- kept final UI restricted to real matched Flibusta `book_id` values
- added discovery cache keys, in-memory daily web limits, and concurrency cap
- added safe `/admin_discovery_status`
- documented Tavily env flags and added mocked discovery tests
- added deterministic intent router and recommendation topic extraction
- routed free text through exact/author/author-title/recommendation/discovery decisions
- filtered generic recommendation anchors before AI expansion
- passed cleaned recommendation context into AI planner
- preserved exact title handling for `Подборка стихотворений`
- added `/admin_intent` dry-run diagnostics and safe intent logging
- expanded `/admin_discovery_status` without live network probes or secret output
- discovery result UI now reports web source only when web snippets were actually used

## Intentionally deferred
- no live Tavily health probe; admin status stays non-networked
- no durable rate-limit table yet; current in-memory daily counter is deliberately lightweight
- router heuristics still need watching for uncommon ambiguous two-word titles
