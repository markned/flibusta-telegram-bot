---
name: insecure-defaults
description: Audit changed configuration or an explicitly scoped codebase for unsafe defaults, fail-open access, fallback secrets and debug leakage.
---

# Insecure defaults (Codex adaptation)

Adapted from Trail of Bits' `insecure-defaults` command and category corpora. This is a serial local workflow; do not invoke Claude Workflow tools or its multi-agent pipeline.

1. Determine the requested file/directory scope and production reachability; inspect relevant deployment configuration without printing secrets. Explicitly named tests are in scope; otherwise exclude fixtures, docs and vendored code.
2. Require all six category `.md` and `.json` files in `references/`. If any is missing, stop and report incomplete installation. Read only the category currently being assessed: fallback-secrets, default-credentials, fail-open-security, weak-crypto, permissive-access, debug-features.
3. Use each JSON seed and derive stack-specific patterns for this project's Python settings wrappers, environment lookups and deployment manifests. Search with `rg`; JSON seeds are POSIX ERE, so use an ERE-compatible tool or carefully translate them. Record seed and derived coverage separately. Grep hits are candidates, not findings.
4. Deduplicate by category:file:line. Trace each candidate with its category's Report when/Skip when discriminator: (a) production reachable? (b) insecure value actually runs? Configurable lookups that fail closed are safe; unconditional insecure values do not need an environment fallback. (c) value insecure? (d) reaches a security decision/sink? (e) does deployment always supply the variable? This last question changes severity only for configurable defaults. Cite the trace; incomplete traces remain unconfirmed, never confirmed findings.
5. Report confirmed findings with file/line, source-to-sink evidence, severity and minimal remediation, plus refuted candidates and coverage gaps. Missing corpus or incomplete analysis is not a clean result. No candidates means no matches within assessed scope, not proof of absence. Keep output compact; write a local Markdown report only when a full audit is requested.

No network calls, secret scanners, external services or extra agents are required. Do not change production configuration as part of an audit.
