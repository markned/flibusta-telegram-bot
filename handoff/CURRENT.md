# Current handoff

## Project shape
- aiogram Telegram bot
- SQLite persistence and migrations
- optional AI recommendation layer
- Kindle delivery through SES SMTP

## Current task plan
Files likely to change:
- `app/main.py`, `app/config.py`
- `app/services/discovery/*`
- `.env.example`, `README.md`
- focused discovery tests

Tests to add/update:
- Tavily request/parse/error handling without network
- `/recommend` stays offline by default
- `/discover` web gating, cache, and safe fallback
- unmatched ideas never render as recommendations
- matched `book_id` results are deduped and bad titles filtered
- admin discovery status hides secrets

Expected behavior:
- recommendations may use cheap model/web discovery, but only catalog matches reach users
- normal exact search remains deterministic and web-free
- web discovery is cached, rate-limited, and one-request-per-command by default
- all fan-out stays explicitly capped for a low-memory VPS

Risks:
- web snippets are noisy; matching must remain conservative
- personal cache keys can fragment if profile data grows
- too much command integration could duplicate the existing AI recommendation path

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

## Intentionally deferred
- no live Tavily health probe; admin status stays non-networked
- no durable rate-limit table yet; current in-memory daily counter is deliberately lightweight
