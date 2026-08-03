from __future__ import annotations

from difflib import SequenceMatcher
import re

from app.flibusta import AuthorResult, SearchResult


def clean_query(query: str) -> str:
    cleaned = query.replace("ё", "е").replace("Ё", "Е")
    cleaned = re.sub(r'[«»"“”„]+', "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def norm(text: str) -> str:
    text = clean_query(text).lower()
    text = re.sub(r"\[[^\]]+\]|\([^)]*\)", "", text)
    text = re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def base_title(title: str) -> str:
    return re.sub(r"\s*(\[[^\]]+\]|\([^)]*\))", "", title).strip()


def rank_and_dedupe_books(results: list[SearchResult], query: str) -> list[SearchResult]:
    query_norm = norm(query)
    deduped: dict[tuple[str, str], SearchResult] = {}
    for item in results:
        key = (norm(base_title(item.title)), norm(item.author or ""))
        current = deduped.get(key)
        if current is None or book_score(item, query_norm) > book_score(current, query_norm):
            deduped[key] = item
    return sorted(
        deduped.values(),
        key=lambda item: book_score(item, query_norm),
        reverse=True,
    )


def book_score(item: SearchResult, query_norm: str) -> tuple[int, int, int, int, int, float]:
    title = norm(base_title(item.title))
    full_title = norm(item.title)
    author = norm(item.author or "")
    query_tokens = set(query_norm.split())
    title_tokens = set(title.split())
    combined_tokens = set(f"{title} {author}".split())
    title_coverage = _coverage(query_tokens, title_tokens)
    combined_coverage = _coverage(query_tokens, combined_tokens)
    similarity = SequenceMatcher(None, query_norm, title).ratio() if query_norm and title else 0.0
    return (
        int(title == query_norm),
        int(title.startswith(query_norm)),
        int(query_norm in full_title),
        int(title_coverage == 100),
        combined_coverage,
        similarity,
    )


def rank_authors(authors: list[AuthorResult], query: str) -> list[AuthorResult]:
    query_norm = norm(query)
    return sorted(
        authors,
        key=lambda item: (
            norm(item.name) == query_norm,
            norm(item.name).startswith(query_norm),
            query_norm in norm(item.name),
            SequenceMatcher(None, query_norm, norm(item.name)).ratio(),
        ),
        reverse=True,
    )


def _coverage(expected: set[str], actual: set[str]) -> int:
    if not expected:
        return 0
    return round(100 * len(expected & actual) / len(expected))
