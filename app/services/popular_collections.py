"""Public rankings are metadata only; every published book comes from Flibusta."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import secrets
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from app.flibusta import SearchResult
from app.services.author_names import names_match
from app.services.search_logic import base_title, norm

logger = logging.getLogger(__name__)
SOURCES = {"flibusta": "Flibusta", "yandex": "Яндекс Книги", "litres": "Литрес"}
RANKING_URLS = {
    "yandex": "https://books.yandex.ru/books/s-popular",
    "litres": "https://www.litres.ru/collections/bestsellery-litres/",
}
BELGRADE = ZoneInfo("Europe/Belgrade")
MAX_RANKED_BOOKS = 30


@dataclass(frozen=True)
class RankedBook:
    title: str
    author: str


def parse_ranking(source: str, html: str) -> list[RankedBook]:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    if source == "yandex":
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.get_text())
            except (ValueError, TypeError):
                continue
            nodes = (
                data
                if isinstance(data, list)
                else data.get("@graph", [data])
                if isinstance(data, dict)
                else []
            )
            for node in nodes:
                if not isinstance(node, dict) or node.get("@type") != "ItemList":
                    continue
                items = node.get("itemListElement", [])
                for entry in sorted(items, key=lambda e: int(e.get("position", 0))):
                    item = entry.get("item", {})
                    if item.get("@type") != "Book":
                        continue
                    authors = item.get("author", [])
                    if isinstance(authors, dict):
                        authors = [authors]
                    author = ", ".join(
                        a.get("name", "") for a in authors if isinstance(a, dict)
                    )
                    rows.append(RankedBook(item.get("name", ""), author))
    elif source == "litres":
        for card in soup.select('[data-testid="art__wrapper"]'):
            title = card.select_one('a[data-testid="art__title"][href^="/book/"]')
            authors = card.select('[data-testid="art__authorName--link"]')
            if title and authors:
                rows.append(
                    RankedBook(
                        title.get_text(" ", strip=True),
                        ", ".join(a.get_text(" ", strip=True) for a in authors),
                    )
                )
    else:
        raise ValueError("Unknown ranking source")
    result, seen = [], set()
    for row in rows:
        key = (norm(row.title), norm(row.author))
        if (
            row.title
            and row.author
            and key not in seen
            and len(row.title) <= 300
            and len(row.author) <= 300
        ):
            result.append(row)
            seen.add(key)
        if len(result) >= MAX_RANKED_BOOKS:
            break
    if not result:
        raise ValueError("Ranking markup unavailable or changed")
    return result


async def fetch_ranking(source: str) -> list[RankedBook]:
    url = RANKING_URLS[source]
    # Two bounded sockets, one request at a time; no browser engine or external API.
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
    ) as client:
        # Yandex's stable public entry redirects to its current collection.
        for _ in range(4):
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    target = response.next_request.url
                    allowed = (
                        {"books.yandex.ru"}
                        if source == "yandex"
                        else {"www.litres.ru", "litres.ru"}
                    )
                    if target.scheme != "https" or target.host not in allowed:
                        raise ValueError("Unexpected ranking redirect")
                    url = str(target)
                    continue
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > 2 * 1024 * 1024:
                        raise ValueError("Ranking response too large")
                    data.extend(chunk)
                return parse_ranking(source, data.decode("utf-8"))
    raise ValueError("Too many ranking redirects")


def next_refresh(now: datetime) -> datetime:
    local = now.astimezone(BELGRADE)
    due = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    return due


class PopularCollections:
    def __init__(self, client, repo, *, fetch=fetch_ranking, pause_seconds=0.5):
        self.client, self.repo, self.fetch = client, repo, fetch
        self.pause_seconds = pause_seconds
        self._lock = asyncio.Lock()
        self._task = None

    async def books(self, source):
        if source not in SOURCES:
            raise ValueError("Unknown collection")
        rows = await self.repo.get_stale("collection:" + source, 7 * 86400)
        return [SearchResult(**row) for row in (rows or [])][:100]

    async def random_book(self, exclude=None):
        books = {}
        for source in SOURCES:
            for book in await self.books(source):
                books[book.book_id] = book
        candidates = (
            [book for book in books.values() if book.book_id != exclude]
            if len(books) > 1
            else list(books.values())
        )
        return secrets.choice(candidates) if candidates else None

    async def refresh(self):
        if self._lock.locked():
            return
        async with self._lock:
            for source in SOURCES:
                try:
                    async with asyncio.timeout(600):
                        if source == "flibusta":
                            books = await self.client.popular_books()
                        else:
                            ranked = await self.fetch(source)
                            books, seen = [], set()
                            matching_failed = False
                            for item in ranked[:MAX_RANKED_BOOKS]:
                                try:
                                    async with asyncio.timeout(12):
                                        candidates = await self.client.search(
                                            item.title, limit=12
                                        )
                                    matched = [
                                        b
                                        for b in candidates
                                        if b.book_id.isdigit()
                                        and b.author
                                        and norm(base_title(b.title))
                                        == norm(base_title(item.title))
                                        and names_match(item.author, b.author)
                                    ]
                                    identities = {norm(b.author) for b in matched}
                                    if matched and len(identities) == 1:
                                        book = matched[0]
                                        key = (
                                            norm(base_title(book.title)),
                                            norm(book.author),
                                        )
                                        if key not in seen:
                                            seen.add(key)
                                            books.append(book)
                                except Exception:
                                    matching_failed = True
                                    logger.info(
                                        "collection matching failed source=%s", source
                                    )
                                await asyncio.sleep(self.pause_seconds)
                            if matching_failed:
                                raise RuntimeError(
                                    "Incomplete catalog matching; keep previous snapshot"
                                )
                        # Publishing is one atomic SQLite upsert. Preserve old data on empty/failure.
                        if books:
                            await self.repo.set(
                                "collection:" + source,
                                "collection",
                                [asdict(b) for b in books[:100]],
                                86400,
                            )
                        logger.info(
                            "collection refresh source=%s matched=%d",
                            source,
                            len(books),
                        )
                except Exception:
                    logger.warning(
                        "collection refresh failed source=%s; retaining snapshot",
                        source,
                        exc_info=True,
                    )

    async def _run(self):
        try:
            if any(
                [
                    await self.repo.get("collection:" + source) is None
                    for source in SOURCES
                ]
            ):
                await self.refresh()
        except Exception:
            logger.exception("collection initial refresh failed")
        while True:
            now = datetime.now(timezone.utc)
            await asyncio.sleep(
                (next_refresh(now).astimezone(timezone.utc) - now).total_seconds()
            )
            try:
                await self.refresh()
            except Exception:
                logger.exception("collection daily refresh failed")

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
