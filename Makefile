.PHONY: test test-search test-kindle search-quality lint check

test:
	.venv/bin/python -m pytest -q

test-search:
	.venv/bin/python -m pytest -q tests/test_flibusta_parser.py tests/test_library_features.py tests/test_search_resolver.py tests/test_search_golden.py

search-quality:
	.venv/bin/python scripts/search_quality.py

test-kindle:
	.venv/bin/python -m pytest -q tests/test_kindle.py

lint:
	.venv/bin/python -m compileall -q app tests
	.venv/bin/python -m ruff check app tests scripts/check_codex_skills.py
	.venv/bin/python scripts/check_codex_skills.py

check: lint test
