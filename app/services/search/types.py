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


    approximate: bool = False
    partial: bool = False
    source_unavailable: bool = False
    stale_at: str | None = None
    has_more: bool = False


def outcome_notice(outcome: SearchOutcome) -> str:
    """Plain text shared by web/Telegram; no raw upstream messages or secrets."""
    if outcome.stale_at:
        from datetime import datetime
        date = datetime.fromisoformat(outcome.stale_at).strftime("%d.%m.%Y %H:%M UTC")
        return f"Результаты из кэша от {date}. Источник временно недоступен."
    if outcome.source_unavailable or outcome.partial:
        return "Источник временно недоступен. Показаны доступные результаты; поиск мог завершиться не полностью."
    return ""
