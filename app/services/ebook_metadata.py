from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.services.covers.types import CoverImage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolishedEbook:
    content: bytes
    filename: str
    format: str
    metadata_changed: bool
    cover_embedded: bool
    tool: str | None


class EbookMetadataPolisher:
    def __init__(self, *, tool: str = "ebook-meta", timeout_seconds: float = 30, require_tool: bool = False):
        self.tool = tool
        self.timeout_seconds = timeout_seconds
        self.require_tool = require_tool

    def tool_available(self) -> bool:
        return shutil.which(self.tool) is not None

    async def polish(
        self,
        *,
        content: bytes,
        filename: str,
        source_format: str,
        title: str,
        authors: list[str],
        cover_image: CoverImage | None,
    ) -> PolishedEbook:
        source_format = (source_format or "").lower()
        if source_format != "epub":
            return PolishedEbook(content, filename, source_format, False, False, None)
        if not self.tool_available():
            if self.require_tool:
                logger.warning("ebook metadata tool is required but missing tool=%s", self.tool)
            return PolishedEbook(content, filename, source_format, False, False, None)

        try:
            with tempfile.TemporaryDirectory(prefix="kindle-meta-") as tmp:
                tmp_dir = Path(tmp)
                epub_path = tmp_dir / "input.epub"
                epub_path.write_bytes(content)
                command = [self.tool, str(epub_path)]
                if title:
                    command += ["--title", title]
                if authors:
                    command += ["--authors", " & ".join(authors)]
                cover_embedded = False
                if cover_image is not None:
                    cover_path = tmp_dir / _cover_temp_name(cover_image.content_type)
                    cover_path.write_bytes(cover_image.content)
                    command += ["--cover", str(cover_path)]
                    cover_embedded = True
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
                except TimeoutError:
                    process.kill()
                    await process.communicate()
                    logger.warning("ebook metadata polish timed out tool=%s", self.tool)
                    return PolishedEbook(content, filename, source_format, False, False, self.tool)
                if process.returncode != 0:
                    logger.warning(
                        "ebook metadata polish failed tool=%s returncode=%s stderr=%s",
                        self.tool,
                        process.returncode,
                        _short(stderr),
                    )
                    return PolishedEbook(content, filename, source_format, False, False, self.tool)
                polished = epub_path.read_bytes()
                return PolishedEbook(polished, filename, source_format, True, cover_embedded, self.tool)
        except Exception:
            logger.warning("ebook metadata polish failed unexpectedly", exc_info=True)
            return PolishedEbook(content, filename, source_format, False, False, self.tool)


def _cover_temp_name(content_type: str) -> str:
    if content_type == "image/png":
        return "cover.png"
    if content_type == "image/webp":
        return "cover.webp"
    return "cover.jpg"


def _short(value: bytes, limit: int = 400) -> str:
    text = value.decode("utf-8", errors="replace")
    text = " ".join(text.split())
    return text[:limit]
