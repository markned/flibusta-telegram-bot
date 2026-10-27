from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import logging
import re
from time import monotonic

from app.flibusta import (
    AuthorResult,
    BookDetails,
    DownloadFormat,
    FlibustaClient,
    FlibustaError,
    SearchResult,
    SeriesRef,
)
from app.repositories.cache import CacheRepository
from app.services.search.context import search_status
from app.services.fuzzy_query import tokens


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StaleValue:
    data: object
    created_at: str


class CachedFlibustaClient:
    def __init__(
        self,
        client: FlibustaClient,
        repo: CacheRepository,
        *,
        enabled: bool,
        ttls: dict[str, int],
        stale_if_error_seconds: int = 604800,
        circuit_breaker_failures: int = 3,
        circuit_breaker_cooldown_seconds: float = 30,
        source_timeout_seconds: float = 25,
        stale_refresh_timeout_seconds: float = 1,
    ) -> None:
        self.client = client
        self.repo = repo
        self.enabled = enabled
        self.ttls = {"popular_books": 900, "genres": 86400, "genre_books": 3600, **ttls}
        self.stale_if_error_seconds = max(0, stale_if_error_seconds)
        self.circuit_breaker_failures = max(1, circuit_breaker_failures)
        self.circuit_breaker_cooldown_seconds = max(0.0, circuit_breaker_cooldown_seconds)
        self.source_timeout_seconds = max(0.05, source_timeout_seconds)
        self.stale_refresh_timeout_seconds = max(0.05, stale_refresh_timeout_seconds)
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        # Identical requests often arrive from Telegram and the web UI at the
        # same time. Share one source call instead of making Flibusta do the
        # same slow work twice. Tasks are removed as soon as they finish.
        self._inflight: dict[str, asyncio.Task] = {}
        self._max_inflight = 64
        self._waiters: dict[asyncio.Task, int] = {}
        self._author_catalog_lock = asyncio.Lock()
        self._author_names = None
        self._author_names_lock = asyncio.Lock()
        self._author_names_dirty = False
        self._author_names_saved = 0.0

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._flush_author_names()
        await self.client.close()

    async def popular_books(self):
        return await self._cached("popular_books", "week", self.client.popular_books,
                                  lambda rows: [SearchResult(**row) for row in rows],
                                  source_timeout_seconds=self.source_timeout_seconds)

    async def genres(self):
        return await self._cached("genres", "grouped-v2", self.client.genres, lambda rows: rows,
                                  source_timeout_seconds=self.source_timeout_seconds)

    async def genre_books(self, code):
        if not re.fullmatch(r"[a-z0-9_]{1,48}", code):
            raise ValueError("Invalid genre")
        return await self._cached("genre_books", code, lambda: self.client.genre_books(code),
                                  lambda rows: [SearchResult(**row) for row in rows],
                                  source_timeout_seconds=self.source_timeout_seconds)

    async def search(self, query: str, limit: int = 8):
        books = await self._cached(
            "book_search",
            f"text-v2:{_cache_query(query)}:{limit}",
            lambda: self.client.search(query, limit),
            lambda rows: [SearchResult(**row) for row in rows],
            source_timeout_seconds=self.source_timeout_seconds,
        )
        await self._learn_author_names(book.author for book in books if book.author)
        return books

    async def search_authors(self, query: str, limit: int = 20):
        authors = await self._cached(
            "author_search",
            f"text-v2:{_cache_query(query)}:{limit}",
            lambda: self.client.search_authors(query, limit),
            lambda rows: [AuthorResult(**row) for row in rows],
            source_timeout_seconds=self.source_timeout_seconds,
        )
        await self._learn_author_names(author.name for author in authors)
        return authors

    async def search_all(self, query: str, book_limit: int = 8, author_limit: int = 20):
        return await self._cached(
            "smart_search",
            f"text-v2:{_cache_query(query)}:{book_limit}:{author_limit}",
            lambda: self.client.search_all(query, book_limit, author_limit),
            lambda pair: (
                [SearchResult(**row) for row in pair[0]],
                [AuthorResult(**row) for row in pair[1]],
            ),
            source_timeout_seconds=self.source_timeout_seconds,
        )

    async def author_works_page(
        self, author_id: str, *, offset: int = 0, limit: int = 40
    ):
        from app.services.works import AuthorWorksPage, group_books

        if not author_id.isdigit() or offset < 0 or not 1 <= limit <= 40:
            raise ValueError("Invalid author page")
        name, books = await self._author_catalog(author_id)
        works = [
            replace(w, source_author_id=author_id)
            for w in group_books(books, alphabetical=True)
        ]
        end = min(len(works), offset + limit)
        return AuthorWorksPage(
            name, works[offset:end], len(works), end, end < len(works)
        )

    async def _author_catalog(self, author_id):
        async def load():
            async with self._author_catalog_lock:
                return await self.client.author_books(author_id, limit=None)

        result = await self._cached(
            "author_books",
            f"{author_id}:catalog-v3",
            load,
            lambda pair: (pair[0], [SearchResult(**row) for row in pair[1]]),
            source_timeout_seconds=self.source_timeout_seconds,
        )
        await self._learn_author_names([result[0]])
        return result

    async def complete_work(self, work):
        from app.services.works import group_books, editions, identity
        from app.services.author_names import names_match

        if not work.source_author_id or not work.source_author_id.isdigit():
            return work
        name, books = await self._author_catalog(work.source_author_id)
        if work.author and not names_match(work.author, name):
            raise FlibustaError("Не удалось подтвердить автора изданий.")
        candidate = next(
            (
                b
                for b in group_books(books)
                if identity(b.title) == identity(work.title)
            ),
            None,
        )
        if candidate is None:
            return work
        merged = group_books(
            [
                SearchResult(
                    e.book_id, e.title, name, source_author_id=work.source_author_id
                )
                for e in (*editions(work), *editions(candidate))
            ]
        )[0]
        return replace(
            merged,
            book_id=work.book_id,
            editions=editions(merged),
            source_author_id=work.source_author_id,
        )

    async def author_books(self, author_id: str, limit: int = 40):
        return await self._cached(
            "author_books",
            f"{author_id}:{limit}",
            lambda: self.client.author_books(author_id, limit),
            lambda pair: (pair[0], [SearchResult(**row) for row in pair[1]]),
            source_timeout_seconds=self.source_timeout_seconds,
        )

    async def series_books(self, series_id: str, limit: int = 80):
        return await self._cached(
            "series_books",
            f"{series_id}:{limit}",
            lambda: self.client.series_books(series_id, limit),
            lambda pair: (pair[0], [SearchResult(**row) for row in pair[1]]),
            source_timeout_seconds=self.source_timeout_seconds,
        )

    async def details(self, book_id: str):
        result = await self._cached(
            "book_details",
            book_id,
            lambda: self.client.details(book_id),
            _details_from_dict,
        )
        await self._learn_author_names(getattr(result, "authors", []))
        return result

    async def _load_author_names(self):
        if self._author_names is not None:
            return True
        try:
            async with asyncio.timeout(0.1):
                names = await self.repo.get("author-name-lexicon:v1") or []
        except Exception:
            logger.debug("author name lexicon read skipped")
            return False
        self._author_names = (
            {
                tuple(sorted(tokens(name))): name
                for name in names[:2048]
                if isinstance(name, str) and 0 < len(name) <= 120
            }
            if isinstance(names, list)
            else {}
        )
        return True

    async def author_name_hints(self):
        if not self.enabled:
            return ()
        async with self._author_names_lock:
            if not await self._load_author_names():
                return ()
            return tuple(reversed(self._author_names.values()))

    async def _learn_author_names(self, names):
        if not self.enabled:
            return
        async with self._author_names_lock:
            if not await self._load_author_names():
                return
            for name in names:
                if not isinstance(name, str) or not 0 < len(name) <= 120:
                    continue
                words = re.findall(r"[a-zа-яё]+", name.casefold())
                if not 1 <= len(words) <= 5 or not any(len(w) >= 3 for w in words):
                    continue
                key = tuple(sorted(tokens(name)))
                if (
                    self._author_names.get(key) != name
                    or next(reversed(self._author_names), None) != key
                ):
                    self._author_names.pop(key, None)
                    self._author_names[key] = name
                    self._author_names_dirty = True
                while len(self._author_names) > 2048:
                    self._author_names.pop(next(iter(self._author_names)))
        if self._author_names_dirty and monotonic() - self._author_names_saved >= 60:
            await self._flush_author_names()

    async def _flush_author_names(self):
        if not self.enabled or not self._author_names_dirty:
            return
        async with self._author_names_lock:
            try:
                self._author_names_saved = monotonic()
                async with asyncio.timeout(0.1):
                    await self.repo.set(
                        "author-name-lexicon:v1",
                        "author_lexicon",
                        list(self._author_names.values()),
                        30 * 86400,
                    )
                self._author_names_dirty = False
                self._author_names_saved = monotonic()
            except Exception:
                logger.debug("author name lexicon write skipped")

    async def catalog_hints(self):
        if not self.enabled:
            return [], []
        books, authors = {}, {}
        try:
            for kind, payload in await self.repo.recent_catalog_metadata():
                rows = payload[1] if kind == "author_books" else payload
                if not isinstance(rows, list):
                    continue
                for row in rows[:40]:
                    try:
                        if kind == "author_search":
                            item = AuthorResult(**row)
                            if len(authors) < 100:
                                authors[item.author_id] = item
                        else:
                            item = SearchResult(**row)
                            if len(books) < 200:
                                books[item.book_id] = item
                    except (TypeError, KeyError):
                        continue
        except Exception:
            logger.warning("catalog hint read failed", exc_info=True)
        return list(books.values()), list(authors.values())

    async def download(self, *args, **kwargs):
        return await self.client.download(*args, **kwargs)

    async def _cached(
        self,
        cache_type,
        key,
        loader,
        decode,
        *,
        source_timeout_seconds: float | None = None,
    ):
        cache_key = f"{cache_type}:{key}"
        if self.enabled:
            try:
                hit = await self.repo.get(cache_key)
                if hit is not None:
                    return decode(hit)
            except Exception:
                logger.warning("cache read failed type=%s", cache_type, exc_info=True)

        stale = None
        if source_timeout_seconds is not None:
            stale = await self._stale(cache_key, cache_type, decode)
        if self._circuit_is_open():
            stale = stale or await self._stale(cache_key, cache_type, decode)
            if stale is not None:
                return self._unwrap(stale)
            raise FlibustaError("Flibusta временно недоступна. Попробуй через минуту.")

        async def load_and_cache():
            try:
                if source_timeout_seconds is None:
                    value = await loader()
                else:
                    refresh_timeout = min(source_timeout_seconds, self.stale_refresh_timeout_seconds) if stale is not None else source_timeout_seconds
                    async with asyncio.timeout(refresh_timeout):
                        value = await loader()
            except Exception:
                self._record_failure()
                fallback = stale or await self._stale(cache_key, cache_type, decode)
                if fallback is not None:
                    logger.info("using stale cache after Flibusta failure type=%s", cache_type)
                    return fallback
                raise

            self._record_success()
            if self.enabled and _worth_caching(value):
                try:
                    await self.repo.set(cache_key, cache_type, value, self.ttls[cache_type])
                except Exception:
                    logger.warning("cache write failed type=%s", cache_type, exc_info=True)
            return value

        return self._unwrap(await self._coalesced(cache_key, load_and_cache))

    @staticmethod
    def _unwrap(value):
        if isinstance(value, _StaleValue):
            status = search_status.get()
            if status is not None:
                status.source_unavailable = True
                if status.stale_at is None or value.created_at < status.stale_at:
                    status.stale_at = value.created_at
            return value.data
        return value

    async def _coalesced(self, cache_key: str, loader):
        task = self._inflight.get(cache_key)
        if task is None:
            if len(self._inflight) >= self._max_inflight:
                return await loader()
            task = asyncio.create_task(loader())
            self._inflight[cache_key] = task

            def cleanup(done: asyncio.Task) -> None:
                if self._inflight.get(cache_key) is done:
                    self._inflight.pop(cache_key, None)
                # Retrieve an exception even when every waiter was cancelled.
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(cleanup)
        self._waiters[task] = self._waiters.get(task, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            remaining = self._waiters.get(task, 1) - 1
            if remaining:
                self._waiters[task] = remaining
            else:
                self._waiters.pop(task, None)
                if not task.done():
                    if self._inflight.get(cache_key) is task:
                        self._inflight.pop(cache_key, None)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def _stale(self, cache_key, cache_type, decode):
        if not self.enabled or self.stale_if_error_seconds <= 0:
            return None
        try:
            hit = await self.repo.get_stale_entry(cache_key, self.stale_if_error_seconds)
            return None if hit is None else _StaleValue(decode(hit[0]), hit[1])
        except Exception:
            logger.warning("stale cache read failed type=%s", cache_type, exc_info=True)
            return None

    def _circuit_is_open(self) -> bool:
        return monotonic() < self._circuit_open_until

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_failures:
            self._circuit_open_until = monotonic() + self.circuit_breaker_cooldown_seconds
            logger.warning("Flibusta circuit opened after repeated failures")

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0


def _details_from_dict(data):
    return BookDetails(
        **{
            **data,
            "author_refs": [AuthorResult(**item) for item in data["author_refs"]],
            "formats": [DownloadFormat(**item) for item in data["formats"]],
            "series": [SeriesRef(**item) for item in data.get("series", [])],
        }
    )


def _cache_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.replace("ё", "е").replace("Ё", "Е").casefold()).strip()


def _worth_caching(value) -> bool:
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(item, list) for item in value):
        return bool(value[0] or value[1])
    return True
