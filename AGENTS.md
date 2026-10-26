# Project rules

Полка is an aiogram + aiohttp bot for a low-memory Oracle VPS. Read
`handoff/CURRENT.md` once for architecture; use `.agents/skills/scoped-change/SKILL.md`
for development routing and `docs/codex-routing.json` for task-specific stages.

- SQLite only; no Redis, PostgreSQL, brokers, vector DBs or external search services.
- Deterministic SearchService and Flibusta are the catalog path. AI recommendations
  and their scheduler were removed; do not restore them as part of routine work.
- Preserve search, downloads, reader delivery, favorites, history and access control.
- Book results require real catalog book_id. Books are temporary; secrets stay private.
- Tests mock external Telegram, Flibusta and SMTP. No real downloads or delivery.
- Keep production reader concurrency 1 and resource bounds in CURRENT.md.
- One agent by default. Delegate only independent substantial work with narrow scopes;
  reuse findings, return compact results, and avoid repeated reviews of unchanged code.
- Run focused checks, then `make check`; report failures honestly. No deploy or merge
  unless separately requested. A main merge triggers the existing production workflow.

## Agent skills

Skills are discovered in `.agents/skills/`; explicit `$skill-name` has priority.
Run only relevant stages; interviews and ticket splitting are not routine overhead.
Planning skills read `docs/agents/issue-tracker.md` (local Markdown, no external writes)
and `docs/agents/domain.md`. Full inventory and update notes: `docs/codex-skills.md`.
Cloud setup: `bash .codex/cloud-setup.sh`; maintenance: `bash .codex/cloud-maintenance.sh`.
Development tooling stays outside production requirements.
