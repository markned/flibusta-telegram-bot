from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BookCover:
    url: str
    source: str
    width: int | None
    height: int | None
    confidence: float
    content_type: str | None = None


@dataclass(frozen=True)
class CoverImage:
    content: bytes
    content_type: str
    width: int | None
    height: int | None
    filename: str
    source_url: str
