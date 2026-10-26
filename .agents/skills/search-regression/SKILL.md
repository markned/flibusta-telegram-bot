---
name: search-regression
description: "Diagnose and verify changes to deterministic title/author search, routing, fallback, cache and golden results."
---

Map app/services/search/, app/services/query_analyzer.py, app/services/intent_router.py, app/services/cached_flibusta.py and affected app/main.py handlers using symbols, not full reads.
Reproduce the failing query offline. Check exact title, author, author+title, F.text and /search routing order and successful result message, deduplication/ranking, bounded variants, total/source deadlines, circuit breaker, fresh/stale cache and empty-response exclusion. Results must retain actual catalog book_id; broad recommendations must ask for title/author rather than reintroduce AI.
Reuse tests/test_search_resolver.py, tests/test_search_golden.py, tests/test_library_features.py and tests/fixtures. Run `make test-search`; use `make search-quality` for golden reporting when relevant. Do not weaken golden expectations to hide regressions. Finish with verification-before-completion and make check.
