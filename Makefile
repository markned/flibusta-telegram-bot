.PHONY: test test-search test-kindle lint check

test:
	.venv/bin/python -m pytest -q

test-search:
	.venv/bin/python -m pytest -q tests/test_flibusta_parser.py tests/test_library_features.py

test-kindle:
	.venv/bin/python -m pytest -q tests/test_kindle.py

lint:
	python3.12 -m compileall -q app tests

check: lint test
