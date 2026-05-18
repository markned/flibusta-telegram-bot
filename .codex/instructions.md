# Codex instructions

Use this repository as a low-memory production bot, not a playground.

## Defaults
- Prefer deterministic logic before AI for exact queries.
- Keep AI calls few, bounded, and cacheable.
- Prefer SQLite migrations over ad-hoc schema edits.
- Do not persist downloaded files.
- Never print tokens, SMTP credentials, or full private e-mails unnecessarily.
- Mock Flibusta, Telegram, SMTP, and OpenAI in tests.

## Useful commands
- `make test`
- `make test-search`
- `make test-kindle`
- `make lint`
- `make check`

## Agent map
- `search-agent`: search routing, parsing, ranking, no-results UX.
- `recommender-agent`: AI planner, recommendation packs, candidate filtering.
- `kindle-agent`: Kindle settings, queue, SES SMTP, delivery UX.
- `db-agent`: SQLite schema, repositories, migrations.
- `reviewer-agent`: correctness/security/regression review.
- `low-memory-reviewer`: RAM, concurrency, disk, queue, and cache pressure review.
