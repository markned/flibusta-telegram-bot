from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict

from app.repositories.cache import CacheRepository
from app.services.covers.providers import CoverProvider
from app.services.covers.types import BookCover

logger = logging.getLogger(__name__)


class CoverResolver:
    def __init__(
        self,
        *,
        cache_repo: CacheRepository | None,
        providers: list[CoverProvider],
        enabled: bool = True,
        cache_ttl_seconds: int = 604800,
        negative_cache_ttl_seconds: int = 86400,
        min_confidence: float = 0.72,
        min_width: int = 300,
        min_height: int = 400,
    ):
        self.cache_repo = cache_repo
        self.providers = providers
        self.enabled = enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self.negative_cache_ttl_seconds = negative_cache_ttl_seconds
        self.min_confidence = min_confidence
        self.min_width = min_width
        self.min_height = min_height

    async def resolve(self, *, title: str, authors: list[str], flibusta_cover_url: str | None = None) -> BookCover | None:
        if not self.enabled or not self.providers:
            return None
        key = self.cache_key(title, authors)
        if self.cache_repo:
            try:
                cached = await self.cache_repo.get(key)
                if cached:
                    if cached.get("negative"):
                        return None
                    cover = BookCover(**cached)
                    return cover if self._acceptable(cover) else None
            except Exception:
                logger.warning("cover cache read failed", exc_info=True)

        for provider in self.providers:
            try:
                cover = await provider.find_cover(title=title, authors=authors, flibusta_cover_url=flibusta_cover_url)
            except Exception:
                logger.warning("cover provider failed source=%s", getattr(provider, "source", "unknown"), exc_info=True)
                continue
            if not cover or not self._acceptable(cover):
                continue
            await self._cache(key, asdict(cover), self.cache_ttl_seconds)
            return cover

        await self._cache(key, {"negative": True}, self.negative_cache_ttl_seconds)
        return None

    def _acceptable(self, cover: BookCover) -> bool:
        if cover.confidence < self.min_confidence:
            return False
        if cover.width is not None and cover.width < self.min_width:
            return False
        if cover.height is not None and cover.height < self.min_height:
            return False
        return True

    async def _cache(self, key: str, payload: dict, ttl: int) -> None:
        if not self.cache_repo:
            return
        try:
            await self.cache_repo.set(key, "cover", payload, ttl)
        except Exception:
            logger.warning("cover cache write failed", exc_info=True)

    @staticmethod
    def cache_key(title: str, authors: list[str]) -> str:
        normalized = _norm(title) + ":" + _norm(" ".join(authors[:3]))
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:20]
        return f"cover:{digest}"


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()
