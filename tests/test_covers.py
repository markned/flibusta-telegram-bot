import asyncio
from pathlib import Path

import pytest

from app.repositories.cache import CacheRepository
from app.repositories.db import Database
from app.services.covers.download import CoverDownloadError, download_cover
from app.services.covers.providers import CoverProvider
from app.services.covers.resolver import CoverResolver
from app.services.covers.types import BookCover


def run(coro):
    return asyncio.run(coro)


class Provider(CoverProvider):
    def __init__(self, cover=None, *, fail=False):
        self.cover = cover
        self.fail = fail
        self.calls = 0
        self.source = "fake"

    async def find_cover(self, *, title, authors, flibusta_cover_url=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.cover


def build_repo(tmp_path: Path):
    db = Database(str(tmp_path / "cache.db"))
    run(db.initialize())
    return CacheRepository(db)


def test_cover_resolver_provider_order_and_cache(tmp_path: Path):
    repo = build_repo(tmp_path)
    first = Provider(None)
    second = Provider(BookCover("https://example.com/cover.jpg", "fake", 400, 600, 0.9))
    resolver = CoverResolver(cache_repo=repo, providers=[first, second], min_confidence=0.72)

    cover = run(resolver.resolve(title="Book", authors=["Author"]))
    assert cover and cover.url == "https://example.com/cover.jpg"
    assert first.calls == 1 and second.calls == 1

    cover = run(resolver.resolve(title="Book", authors=["Author"]))
    assert cover and cover.url == "https://example.com/cover.jpg"
    assert second.calls == 1


def test_cover_resolver_negative_cache(tmp_path: Path):
    repo = build_repo(tmp_path)
    provider = Provider(None)
    resolver = CoverResolver(cache_repo=repo, providers=[provider])
    assert run(resolver.resolve(title="Missing", authors=[])) is None
    assert run(resolver.resolve(title="Missing", authors=[])) is None
    assert provider.calls == 1


def test_cover_resolver_rejects_low_confidence_and_provider_failure(tmp_path: Path):
    repo = build_repo(tmp_path)
    bad = Provider(BookCover("https://example.com/cover.jpg", "fake", 400, 600, 0.5))
    failing = Provider(fail=True)
    resolver = CoverResolver(cache_repo=repo, providers=[failing, bad], min_confidence=0.72)
    assert run(resolver.resolve(title="Book", authors=[])) is None
    assert failing.calls == 1 and bad.calls == 1


class FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeResponse:
    def __init__(self, content: bytes, content_type: str):
        self.headers = {"content-type": content_type, "content-length": str(len(content))}
        self._content = content

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size):
        yield self._content


class FakeClient:
    def __init__(self, response):
        self.response = response

    def stream(self, method, url):
        return FakeStream(self.response)


def test_download_cover_rejects_non_http_url():
    with pytest.raises(CoverDownloadError):
        run(download_cover("file:///tmp/cover.jpg", max_bytes=1000, timeout=1, client=FakeClient(None)))


def test_download_cover_accepts_jpeg():
    image = run(download_cover(
        "https://example.com/cover.jpg",
        max_bytes=1000,
        timeout=1,
        client=FakeClient(FakeResponse(b"jpeg-bytes", "image/jpeg")),
    ))
    assert image.content == b"jpeg-bytes"
    assert image.content_type == "image/jpeg"
    assert image.filename == "cover.jpg"


def test_download_cover_rejects_html_and_max_bytes():
    with pytest.raises(CoverDownloadError):
        run(download_cover(
            "https://example.com/cover.jpg",
            max_bytes=1000,
            timeout=1,
            client=FakeClient(FakeResponse(b"<html></html>", "text/html")),
        ))
    with pytest.raises(CoverDownloadError):
        run(download_cover(
            "https://example.com/cover.jpg",
            max_bytes=3,
            timeout=1,
            client=FakeClient(FakeResponse(b"1234", "image/png")),
        ))
