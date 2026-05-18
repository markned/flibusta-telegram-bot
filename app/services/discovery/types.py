from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    source: str

@dataclass(frozen=True)
class BookIdea:
    title: str | None
    author: str | None
    search_query_ru: str
    why_it_may_fit: str | None
    source: str

@dataclass(frozen=True)
class MatchedBook:
    book_id: str
    title: str
    author: str | None
    reason: str | None
    source: str
    score: float

@dataclass(frozen=True)
class DiscoveryResult:
    query: str
    mode: str
    books: list[MatchedBook]
    note: str | None = None
