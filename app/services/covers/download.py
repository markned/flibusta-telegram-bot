from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.services.covers.types import CoverImage

ACCEPTED_TYPES = {"image/jpeg", "image/png", "image/webp"}
CHUNK_SIZE = 64 * 1024


class CoverDownloadError(RuntimeError):
    pass


async def download_cover(url: str, *, max_bytes: int, timeout: float, client: httpx.AsyncClient | None = None) -> CoverImage:
    _validate_cover_url(url)
    close_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
    )
    try:
        async with active_client.stream("GET", url) as response:
            response.raise_for_status()
            final_url = str(getattr(response, "url", "") or url)
            _validate_cover_url(final_url)
            content_type = _clean_content_type(response.headers.get("content-type"))
            if content_type not in ACCEPTED_TYPES:
                raise CoverDownloadError("Cover URL did not return a supported image.")
            content_length = _int_header(response.headers.get("content-length"))
            if content_length and content_length > max_bytes:
                raise CoverDownloadError("Cover image is too large.")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                total += len(chunk)
                if total > max_bytes:
                    raise CoverDownloadError("Cover image is too large.")
                chunks.append(chunk)
        content = b"".join(chunks)
        width, height = _dimensions(content)
        return CoverImage(
            content=content,
            content_type=content_type,
            width=width,
            height=height,
            filename=_cover_filename(url, content_type),
            source_url=url,
        )
    except httpx.TimeoutException as exc:
        raise CoverDownloadError("Cover download timed out.") from exc
    except httpx.HTTPError as exc:
        raise CoverDownloadError("Cover download failed.") from exc
    finally:
        if close_client:
            await active_client.aclose()


def _validate_cover_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise CoverDownloadError("Unsupported cover URL scheme.")
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise CoverDownloadError("Local cover URLs are not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise CoverDownloadError("Private cover URLs are not allowed.")


def _clean_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _int_header(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _cover_filename(url: str, content_type: str) -> str:
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(content_type, ".jpg")
    stem = Path(urlparse(url).path).stem or "cover"
    return f"{stem[:60]}{suffix}"


def _dimensions(content: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image  # type: ignore
        from io import BytesIO

        with Image.open(BytesIO(content)) as image:
            return image.size
    except Exception:
        return None, None
