"""Benchmark fixtures are reproducible and cannot masquerade as live coverage."""
import asyncio

import pytest

from scripts.search_benchmark import RecordingCatalog, build_cases, load_books


def test_pinned_population_produces_thirty_books_and_ninety_distinct_cases():
    dataset, books = load_books()
    cases = build_cases(books)
    assert len(dataset["population"]) == 100
    assert len({b["rank"] for b in books}) == 30
    assert len(cases) == 90
    assert len({c["query"] for c in cases}) == 90
    assert {c["kind"] for c in cases} == {"title", "author_title", "typo"}
    assert all("book_id" not in c for c in cases)  # No invented catalog identifiers.


def test_incomplete_replay_reports_coverage_gap_instead_of_empty_catalog():
    with pytest.raises(RuntimeError, match="Replay does not cover"):
        asyncio.run(RecordingCatalog(responses={}).search("Дюна"))
