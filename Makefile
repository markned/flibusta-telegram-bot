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
	python3.12 -m compileall -q app tests

check: lint test
