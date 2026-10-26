# Issue tracker: Local Markdown

Specs: `.scratch/<feature-slug>/spec.md`.
Tickets: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, one per vertical slice,
numbered in dependency order. Each records `Status: ready-for-agent`, acceptance
criteria and `Blocked by: NN, NN` (or None). Never create GitHub Issues by default.
Triage vocabulary, if needed: needs-triage, needs-info, ready-for-agent,
ready-for-human, wontfix. No external labels are created by setup.
“Publish” means write these local files. “Fetch” means read the referenced file.
To Tickets applies only when work cannot reasonably finish in one session.
