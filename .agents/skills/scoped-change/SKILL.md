---
name: scoped-change
description: "Scope development using diffs, symbols and imports; choose focused skills and tests for implementation tasks."
---

Start with `git status --short`, `git diff --stat`, then the relevant diff. Use `rg -n` for changed symbols and imports; read callers and narrow line ranges before whole files. Read handoff/CURRENT.md once per task. Reuse findings already in this session.
Classify using docs/codex-routing.json; select only relevant stages. Explicit user skill wins. One primary agent by default; parallelize only independent substantial work, with disjoint write scopes and a short findings budget. Never spawn agents for a small fix or routine review.
Use existing Make targets and fixtures. For a bug use systematic-debugging, reproduce with a failing focused test, make the minimal fix, then verification-before-completion. TDD applies to behavior changes, not mechanical docs installation. Do not restart planning once implementation is adequately specified.
Report changed behavior, focused checks, and material limitations concisely; do not repeat full unchanged-code reviews.
