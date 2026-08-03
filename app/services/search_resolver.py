from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from app.flibusta import AuthorResult, SearchResult
from app.services.search_logic import fallback_queries, rank_and_dedupe_books, rank_authors


class SearchClient(Protocol):
    async def search_all(
        self,
        query: str,
        book_limit: int = 8,
        author_limit: int = 20,
    ) -> tuple[list[SearchResult], list[AuthorResult]]: ...


@dataclass(frozen=True)
class SearchResolution:
    query: str
    used_query: str
    books: list[SearchResult]
    authors: list[AuthorResult]
    tried_queries: tuple[str, ...]


async def resolve_search(
    client: SearchClient,
    query: str,
    *,
    book_limit: int,
    author_limit: int,
    timeout_seconds: float = 12,
    max_fallback_queries: int = 2,
) -> SearchResolution:
    """Resolve a query within one wall-clock budget and a bounded fallback set."""

    tried = [query]
    async with asyncio.timeout(max(0.05, timeout_seconds)):
        books, authors = await client.search_all(
            query,
            book_limit=book_limit,
            author_limit=author_limit,
        )
        if books or authors:
            return _resolution(query, query, books, authors, tried)

        best = _resolution(query, query, [], [], tried)
        for candidate in fallback_queries(query)[: max(0, max_fallback_queries)]:
            tried.append(candidate)
            candidate_books, candidate_authors = await client.search_all(
                candidate,
                book_limit=book_limit,
                author_limit=author_limit,
            )
            current = _resolution(query, candidate, candidate_books, candidate_authors, tried)
            if _quality(current) > _quality(best):
                best = current
            if current.books or current.authors:
                break
        return SearchResolution(
            query=best.query,
            used_query=best.used_query,
            books=best.books,
            authors=best.authors,
            tried_queries=tuple(tried),
        )


def _resolution(
    original_query: str,
    used_query: str,
    books: list[SearchResult],
    authors: list[AuthorResult],
    tried: list[str],
) -> SearchResolution:
    return SearchResolution(
        query=original_query,
        used_query=used_query,
        books=rank_and_dedupe_books(books, original_query),
        authors=rank_authors(authors, original_query),
        tried_queries=tuple(tried),
    )


def _quality(result: SearchResolution) -> tuple[int, int]:
    return (int(bool(result.books)) + int(bool(result.authors)), len(result.books) + len(result.authors))
