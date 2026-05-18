# reviewer-agent
Review for regressions, unsafe logs, broken fallbacks, missing tests, and behavior drift.

Checklist:
- exact search still works without AI;
- AI failure falls back safely;
- recommendations use real `book_id` values;
- secrets are not logged;
- no real network in tests;
- user-visible copy still makes sense.
