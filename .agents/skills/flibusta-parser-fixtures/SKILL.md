---
name: flibusta-parser-fixtures
description: "Test Flibusta HTML parsing against local fixtures and mocked responses without catalog network calls."
---

Locate affected parser symbols in app/flibusta.py and cover services, then reuse tests/fixtures and tests/test_flibusta_parser.py, tests/test_covers.py. Add minimal HTML fixtures for malformed/nested/unusual markup, empty or partial pages, missing metadata, redirects and relative/absolute links.
Check title/author roles (exclude translators/illustrators), real book_id extraction, series, covers versus icons, available format codes/links and safe empty results. Mock transport redirects/errors; never fetch fixture content during unit tests. Do not silently accept a login/error page as a book.
Run `.venv/bin/python -m pytest -q tests/test_flibusta_parser.py tests/test_covers.py`; use make test-search if search extraction changes, then make check.
