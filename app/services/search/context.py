"""Request-local catalog provenance; never shared between users or searches."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class SearchStatus:
    stale_at: str | None = None
    source_unavailable: bool = False


search_status: ContextVar[SearchStatus | None] = ContextVar("search_status", default=None)
