# search-agent
Owns deterministic search behavior: `app/main.py` search paths, `app/services/search_logic.py`, `app/services/query_analyzer.py`, parser-facing search regressions, and search tests.

Priorities:
- Exact search must work without AI.
- Prefer title/author fast paths before fuzzy logic.
- Preserve real Flibusta `book_id` results.
- Avoid real network in tests.

Do not edit Kindle, SMTP, or migrations unless the task explicitly requires it.
