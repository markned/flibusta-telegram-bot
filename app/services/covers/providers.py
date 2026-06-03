from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from app.services.covers.types import BookCover


class CoverProvider(ABC):
    source = "unknown"

    @abstractmethod
    async def find_cover(self, *, title: str, authors: list[str], flibusta_cover_url: str | None = None) -> BookCover | None:
        raise NotImplementedError


class DisabledCoverProvider(CoverProvider):
    source = "disabled"

    async def find_cover(self, *, title: str, authors: list[str], flibusta_cover_url: str | None = None) -> BookCover | None:
        return None


class FlibustaCoverProvider(CoverProvider):
    source = "flibusta"

    async def find_cover(self, *, title: str, authors: list[str], flibusta_cover_url: str | None = None) -> BookCover | None:
        if not flibusta_cover_url:
            return None
        parsed = urlparse(flibusta_cover_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        return BookCover(url=flibusta_cover_url, source=self.source, width=None, height=None, confidence=0.92)


class OpenLibraryCoverProvider(CoverProvider):
    source = "openlibrary"

    def __init__(self, *, timeout_seconds: float = 6):
        self.timeout_seconds = timeout_seconds

    async def find_cover(self, *, title: str, authors: list[str], flibusta_cover_url: str | None = None) -> BookCover | None:
        query = " ".join([title, *(authors[:1])]).strip()
        if not query:
            return None
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(
                "https://openlibrary.org/search.json",
                params={"q": query, "limit": 3, "fields": "title,author_name,cover_i"},
            )
            response.raise_for_status()
            docs = response.json().get("docs") or []
        for doc in docs:
            cover_id = doc.get("cover_i")
            if not cover_id:
                continue
            confidence = 0.78 if _author_matches(doc.get("author_name") or [], authors) else 0.73
            return BookCover(
                url=f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg",
                source=self.source,
                width=None,
                height=None,
                confidence=confidence,
                content_type="image/jpeg",
            )
        return None


class GoogleBooksCoverProvider(CoverProvider):
    source = "google_books"

    def __init__(self, *, api_key: str | None = None, timeout_seconds: float = 6):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def find_cover(self, *, title: str, authors: list[str], flibusta_cover_url: str | None = None) -> BookCover | None:
        query_parts = []
        if title:
            query_parts.append(f"intitle:{title}")
        if authors:
            query_parts.append(f"inauthor:{authors[0]}")
        query = " ".join(query_parts).strip() or title.strip()
        if not query:
            return None
        params = {"q": query, "maxResults": 3, "printType": "books"}
        if self.api_key:
            params["key"] = self.api_key
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get("https://www.googleapis.com/books/v1/volumes", params=params)
            response.raise_for_status()
            items = response.json().get("items") or []
        for item in items:
            info = item.get("volumeInfo") or {}
            links = info.get("imageLinks") or {}
            url = links.get("large") or links.get("medium") or links.get("thumbnail")
            if not url:
                continue
            url = url.replace("http://", "https://", 1)
            confidence = 0.77 if _author_matches(info.get("authors") or [], authors) else 0.72
            return BookCover(url=url, source=self.source, width=None, height=None, confidence=confidence)
        return None


def _author_matches(candidate_authors: list[str], authors: list[str]) -> bool:
    if not candidate_authors or not authors:
        return False
    wanted = " ".join(authors).casefold()
    return any(author.casefold() in wanted or wanted in author.casefold() for author in candidate_authors)
