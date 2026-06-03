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
- centralized discovery web activation behind safe `Settings` properties
- added config regression tests so `DISCOVERY_*` env vars load without forcing Tavily on

## Completed in this pass
- fixed broken book-card callback path
- added confirmation-gated broad recommendations with tiny TTL pending store
- made author-title routing case-insensitive
- added neutral recommendation clarifier and disabled literary-source abstraction

## Intentionally deferred
- no live Tavily health probe; admin status stays non-networked
- no durable rate-limit table yet; current in-memory daily counter is deliberately lightweight
- router heuristics still need watching for uncommon ambiguous two-word titles

## Completed in Gmail SMTP / Kindle UX pass
- switched SMTP defaults from SES-first to generic `custom`, with provider presets for `gmail`, `google_workspace`, `zoho`, `brevo`, `mailgun`, `amazon_ses`, and `disabled`
- added effective SMTP config helpers and safe startup/admin diagnostics without SMTP secrets
- added Gmail-oriented `.env.gmail.example`, full `.env.production.example`, and `docs/prod-env-gmail.md`
- redesigned Kindle menu to be button-first: save e-mail, show sender, format selector, sender-confirmed flag, test e-mail, history, remove
- added SQLite migration `006_add_kindle_sender_confirmation` and repository support for `approved_sender_confirmed`
- changed Kindle e-mail body to private-library wording and generic SMTP/Gmail error copy
- updated README away from SES-first deployment guidance
- tests: `make check` ✅ 88 passed

## Deployment note
Use `SMTP_PROVIDER=gmail`, host `smtp.gmail.com`, port `587`, STARTTLS true, and paste the Google app password only into the server `.env`. Do not commit or document the real password.

## Completed in cover UI / Kindle metadata pass
- added `BookDetails.cover_url` and best-effort Flibusta cover extraction heuristics
- added lightweight cover resolver modules with provider order, SQLite metadata cache, negative cache, and safe cover downloader
- book cards now send photo+caption when a reliable cover is available, falling back to text on any failure
- added EPUB-only Kindle metadata polishing through optional Calibre `ebook-meta`; no `ebook-convert`, no FB2→EPUB conversion
- Kindle EPUB sends best-effort clean filename/title/authors and optional embedded cover; raw file is sent if polishing fails or Calibre is missing
- added admin Kindle diagnostics for cover lookup and metadata tool availability
- updated env templates and README with cover/metadata settings and optional `sudo apt install -y calibre`
- tests: `make check` ✅ 110 passed

## Cover/metadata operational notes
- no image bytes are cached in SQLite or memory; only cover metadata/negative results are cached
- `COVER_PROVIDER_ORDER=flibusta` is the fastest conservative setting if external cover lookup feels slow
- keep `KINDLE_WORKER_CONCURRENCY=1` on the low-memory VPS
