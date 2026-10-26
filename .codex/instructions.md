# Compatibility entry point

`AGENTS.md` is the canonical always-loaded project policy. This file is a pointer
for older integrations, not an additional automatically discovered Codex instruction file.
Use `.agents/skills/` for workflows, `.codex/agents/*.toml` for optional specialists,
and `docs/codex-skills.md` for discovery, routing, setup and diagnostics.

Commands remain `make test`, `make test-search`, `make test-kindle`, `make lint`,
`make check`. Do not copy production secrets; tests require no real Telegram token.
