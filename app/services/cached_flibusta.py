from __future__ import annotations

import asyncio
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


logger = logging.getLogger(__name__)


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
    ) -> None:
        self.client = client
        self.repo = repo
        self.enabled = enabled
        self.ttls = ttls
        self.stale_if_error_seconds = max(0, stale_if_error_seconds)
        self.circuit_breaker_failures = max(1, circuit_breaker_failures)
        self.circuit_breaker_cooldown_seconds = max(0.0, circuit_breaker_cooldown_seconds)
        self.source_timeout_seconds = max(0.05, source_timeout_seconds)
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        # Identical requests often arrive from Telegram and the web UI at the
        # same time. Share one source call instead of making Flibusta do the
        # same slow work twice. Tasks are removed as soon as they finish.
        self._inflight: dict[str, asyncio.Task] = {}
        self._max_inflight = 64

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()

    async def search(self, query: str, limit: int = 8):
        return await self._cached(
            "book_search",
            f"{_cache_query(query)}:{limit}",
            lambda: self.client.search(query, limit),
            lambda rows: [SearchResult(**row) for row in rows],
            source_timeout_seconds=self.source_timeout_seconds,
        )

    async def search_authors(self, query: str, limit: int = 20):
        return await self._cached(
            "author_search",
            f"{_cache_query(query)}:{limit}",
            lambda: self.client.search_authors(query, limit),
            lambda rows: [AuthorResult(**row) for row in rows],
            source_timeout_seconds=self.source_timeout_seconds,
        )

    async def search_all(self, query: str, book_limit: int = 8, author_limit: int = 20):
        return await self._cached(
            "smart_search",
            f"{_cache_query(query)}:{book_limit}:{author_limit}",
            lambda: self.client.search_all(query, book_limit, author_limit),
            lambda pair: (
                [SearchResult(**row) for row in pair[0]],
                [AuthorResult(**row) for row in pair[1]],
            ),
            source_timeout_seconds=self.source_timeout_seconds,
        )

    async def author_books(self, author_id: str, limit: int = 40):
        return await self._cached(
            "author_books",
            f"{author_id}:{limit}",
            lambda: self.client.author_books(author_id, limit),
            lambda pair: (pair[0], [SearchResult(**row) for row in pair[1]]),
            source_timeout_seconds=self.source_timeout_seconds,
        )

    async def details(self, book_id: str):
        return await self._cached(
            "book_details",
            book_id,
            lambda: self.client.details(book_id),
            _details_from_dict,
        )

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

        if self._circuit_is_open():
            stale = await self._stale(cache_key, cache_type, decode)
            if stale is not None:
                return stale
            raise FlibustaError("Flibusta временно недоступна. Попробуй через минуту.")

        async def load_and_cache():
            try:
                if source_timeout_seconds is None:
                    value = await loader()
                else:
                    async with asyncio.timeout(source_timeout_seconds):
                        value = await loader()
            except Exception:
                self._record_failure()
                stale = await self._stale(cache_key, cache_type, decode)
                if stale is not None:
                    logger.info("using stale cache after Flibusta failure type=%s", cache_type)
                    return stale
                raise

            self._record_success()
            if self.enabled and _worth_caching(value):
                try:
                    await self.repo.set(cache_key, cache_type, value, self.ttls[cache_type])
                except Exception:
                    logger.warning("cache write failed type=%s", cache_type, exc_info=True)
            return value

        return await self._coalesced(cache_key, load_and_cache)

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
        return await asyncio.shield(task)

    async def _stale(self, cache_key, cache_type, decode):
        if not self.enabled or self.stale_if_error_seconds <= 0:
            return None
        try:
            hit = await self.repo.get_stale(cache_key, self.stale_if_error_seconds)
            return None if hit is None else decode(hit)
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
