import asyncio

from app.services.covers.types import CoverImage
from app.services.ebook_metadata import EbookMetadataPolisher


def run(coro):
    return asyncio.run(coro)


def test_polisher_missing_tool_returns_original(monkeypatch):
    monkeypatch.setattr("app.services.ebook_metadata.shutil.which", lambda tool: None)
    polisher = EbookMetadataPolisher(tool="ebook-meta")
    result = run(polisher.polish(content=b"epub", filename="book.epub", source_format="epub", title="Title", authors=["Author"], cover_image=None))
    assert result.content == b"epub"
    assert result.metadata_changed is False


def test_polisher_non_epub_bypasses(monkeypatch):
    monkeypatch.setattr("app.services.ebook_metadata.shutil.which", lambda tool: "/usr/bin/ebook-meta")
    polisher = EbookMetadataPolisher(tool="ebook-meta")
    result = run(polisher.polish(content=b"fb2", filename="book.fb2", source_format="fb2", title="Title", authors=["Author"], cover_image=None))
    assert result.content == b"fb2"
    assert result.metadata_changed is False


class FakeProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return b"", b""

    def kill(self):
        self.killed = True


def test_polisher_command_includes_title_authors_and_cover(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.services.ebook_metadata.shutil.which", lambda tool: "/usr/bin/ebook-meta")

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProcess(0)

    monkeypatch.setattr("app.services.ebook_metadata.asyncio.create_subprocess_exec", fake_exec)
    cover = CoverImage(b"cover", "image/jpeg", None, None, "cover.jpg", "https://example.com/cover.jpg")
    polisher = EbookMetadataPolisher(tool="ebook-meta")
    result = run(polisher.polish(content=b"epub", filename="book.epub", source_format="epub", title="Title", authors=["A", "B"], cover_image=cover))
    cmd = captured["cmd"]
    assert cmd[0] == "ebook-meta"
    assert "--title" in cmd and "Title" in cmd
    assert "--authors" in cmd and "A & B" in cmd
    assert "--cover" in cmd
    assert "shell" not in captured["kwargs"]
    assert result.metadata_changed is True
    assert result.cover_embedded is True


def test_polisher_timeout_returns_original(monkeypatch):
    monkeypatch.setattr("app.services.ebook_metadata.shutil.which", lambda tool: "/usr/bin/ebook-meta")

    class SlowProcess(FakeProcess):
        async def communicate(self):
            if self.killed:
                return b"", b""
            await asyncio.sleep(10)
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        return SlowProcess(0)

    monkeypatch.setattr("app.services.ebook_metadata.asyncio.create_subprocess_exec", fake_exec)
    polisher = EbookMetadataPolisher(tool="ebook-meta", timeout_seconds=0.01)
    result = run(polisher.polish(content=b"epub", filename="book.epub", source_format="epub", title="Title", authors=[], cover_image=None))
    assert result.content == b"epub"
    assert result.metadata_changed is False
