from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.flibusta import AuthorResult, SearchResult


class SearchMode(str, Enum):
    EXACT = "exact"
    AUTHOR = "author"
    AUTHOR_TITLE = "author_title"
    UNSUPPORTED_TOPIC = "unsupported_topic"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class SearchPlan:
    original_query: str
    cleaned_query: str
    mode: SearchMode
    primary_queries: tuple[str, ...]
    fallback_queries: tuple[str, ...]
    author: str | None = None
    title: str | None = None
    format_hint: str | None = None


@dataclass(frozen=True)
class SearchOutcome:
    plan: SearchPlan
    books: list[SearchResult]
    authors: list[AuthorResult]
    used_queries: tuple[str, ...]

