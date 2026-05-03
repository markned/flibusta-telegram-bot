from __future__ import annotations

import html
import logging
import re
import zipfile
from asyncio import sleep
from io import BytesIO
from dataclasses import dataclass
from time import monotonic
from typing import Iterable
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
DOWNLOAD_CHUNK_SIZE = 256 * 1024
DOWNLOAD_PROGRESS_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class SearchResult:
    book_id: str
    title: str
    author: str | None


@dataclass(frozen=True)
class AuthorResult:
    author_id: str
    name: str


@dataclass(frozen=True)
class DownloadFormat:
    code: str
    label: str
    url: str


@dataclass(frozen=True)
class BookDetails:
    book_id: str
    title: str
    authors: list[str]
    author_refs: list[AuthorResult]
    genres: list[str]
    file_size: str | None
    pages: int | None
    annotation: str | None
    formats: list[DownloadFormat]
    page_url: str


class FlibustaError(RuntimeError):
    pass


class FlibustaClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 25,
        proxy: str | None = None,
        retries: int = 3,
        retry_delay: float = 1.5,
        max_redirects: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.retries = max(1, retries)
        self.retry_delay = retry_delay
        self.max_redirects = max(1, max_redirects)
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy or None,
            follow_redirects=False,
            http2=True,
            headers={
                "User-Agent": "Mozilla/5.0 flibusta-bot/0.1",
                "Accept-Language": "ru,en;q=0.8",
            },
        )

    async def close(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        query = query.strip()
        if not query:
            return []

        url = f"{self.base_url}/booksearch?ask={quote_plus(query)}"
        response = await self._get(url)
        return parse_search_results(response.text, limit=limit)

    async def search_authors(self, query: str, limit: int = 20) -> list[AuthorResult]:
        query = query.strip()
        if not query:
            return []

        url = f"{self.base_url}/booksearch?ask={quote_plus(query)}"
        response = await self._get(url)
        return parse_author_results(response.text, limit=limit)

    async def author_books(self, author_id: str, limit: int = 40) -> tuple[str, list[SearchResult]]:
        page_url = urljoin(self.base_url + "/", f"a/{author_id}")
        response = await self._get(page_url)
        return parse_author_page(response.text, author_id, limit=limit)

    async def details(self, book_id: str) -> BookDetails:
        page_url = urljoin(self.base_url + "/", f"b/{book_id}")
        response = await self._get(page_url)
        return parse_book_details(response.text, self.base_url, book_id, page_url)

    async def download(self, url: str, max_bytes: int) -> tuple[bytes, str, str]:
        response, content = await self._download(url, max_bytes)
        logger.debug(
            "Flibusta download response: url=%s status=%s content_type=%s content_length=%s",
            _safe_log_url(str(response.url)),
            response.status_code,
            response.headers.get("content-type"),
            response.headers.get("content-length"),
        )
        filename = _filename_from_response(response) or _filename_from_url(url)
        content_type = response.headers.get("content-type", "application/octet-stream")
        content, filename, content_type = _unzip_fb2_if_needed(content, filename, content_type, max_bytes)
        return content, filename, content_type

    async def _get(self, url: str) -> httpx.Response:
        last_error: httpx.HTTPError | None = None

        for attempt in range(1, self.retries + 1):
            try:
                response = await self._get_following_redirects(url)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise FlibustaError(f"Flibusta вернула HTTP {status_code}.") from exc
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.retries:
                    raise FlibustaError("Не удалось подключиться к Flibusta.") from exc

            logger.warning("Flibusta request failed, retrying (%d/%d): %s", attempt, self.retries, _safe_log_url(url))
            await sleep(self.retry_delay * attempt)

        raise FlibustaError("Не удалось подключиться к Flibusta.") from last_error

    async def _download(self, url: str, max_bytes: int) -> tuple[httpx.Response, bytes]:
        last_error: httpx.HTTPError | None = None

        for attempt in range(1, self.retries + 1):
            try:
                return await self._download_following_redirects(url, max_bytes)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise FlibustaError(f"Flibusta вернула HTTP {status_code}.") from exc
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.retries:
                    raise FlibustaError("Не удалось скачать файл с Flibusta.") from exc

            logger.warning("Flibusta download failed, retrying (%d/%d): %s", attempt, self.retries, _safe_log_url(url))
            await sleep(self.retry_delay * attempt)

        raise FlibustaError("Не удалось скачать файл с Flibusta.") from last_error

    async def _get_following_redirects(self, url: str) -> httpx.Response:
        current_url = url
        seen_urls: set[str] = set()

        for redirect_index in range(self.max_redirects + 1):
            response = await self._client.get(current_url)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response

            location = response.headers.get("location")
            if not location:
                return response

            next_url = urljoin(str(response.url), location)
            logger.debug(
                "Flibusta redirect %d/%d: %s -> %s",
                redirect_index + 1,
                self.max_redirects,
                _safe_log_url(str(response.url)),
                _safe_log_url(next_url),
            )

            if next_url in seen_urls:
                raise FlibustaError("Flibusta зациклила redirect при скачивании.")

            seen_urls.add(next_url)
            current_url = next_url

        raise FlibustaError("Слишком много redirect от Flibusta при скачивании.")

    async def _download_following_redirects(self, url: str, max_bytes: int) -> tuple[httpx.Response, bytes]:
        current_url = url
        seen_urls: set[str] = set()

        for redirect_index in range(self.max_redirects + 1):
            async with self._client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                        return response, b""

                    next_url = urljoin(str(response.url), location)
                    logger.debug(
                        "Flibusta redirect %d/%d: %s -> %s",
                        redirect_index + 1,
                        self.max_redirects,
                        _safe_log_url(str(response.url)),
                        _safe_log_url(next_url),
                    )

                    if next_url in seen_urls:
                        raise FlibustaError("Flibusta зациклила redirect при скачивании.")

                    seen_urls.add(next_url)
                    current_url = next_url
                    continue

                response.raise_for_status()
                content_length = _int_header(response.headers.get("content-length"))
                if content_length and content_length > max_bytes:
                    raise FlibustaError("Файл больше лимита Telegram для этого бота.")

                logger.debug(
                    "Flibusta download stream start: url=%s content_type=%s content_length=%s",
                    _safe_log_url(str(response.url)),
                    response.headers.get("content-type"),
                    response.headers.get("content-length"),
                )

                chunks: list[bytes] = []
                downloaded = 0
                last_progress_at = monotonic()
                async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise FlibustaError("Файл больше лимита Telegram для этого бота.")

                    chunks.append(chunk)
                    now = monotonic()
                    if now - last_progress_at >= DOWNLOAD_PROGRESS_INTERVAL_SECONDS:
                        logger.debug(
                            "Flibusta download progress: url=%s downloaded=%s content_length=%s",
                            _safe_log_url(str(response.url)),
                            downloaded,
                            content_length or "unknown",
                        )
                        last_progress_at = now

                logger.debug(
                    "Flibusta download stream done: url=%s downloaded=%s content_length=%s",
                    _safe_log_url(str(response.url)),
                    downloaded,
                    content_length or "unknown",
                )
                return response, b"".join(chunks)

        raise FlibustaError("Слишком много redirect от Flibusta при скачивании.")


def parse_search_results(markup: str, limit: int = 8) -> list[SearchResult]:
    soup = BeautifulSoup(markup, "lxml")
    results: list[SearchResult] = []
    seen: set[str] = set()

    for link in soup.select('a[href^="/b/"], a[href^="b/"]'):
        href = link.get("href", "")
        match = re.search(r"/?b/(\d+)$", href)
        if not match:
            continue
        book_id = match.group(1)
        if book_id in seen:
            continue

        title = _clean_text(link.get_text(" ", strip=True))
        if not title:
            continue

        author = _find_nearby_author(link)
        results.append(SearchResult(book_id=book_id, title=title, author=author))
        seen.add(book_id)

        if len(results) >= limit:
            break

    return results


def parse_author_results(markup: str, limit: int = 20) -> list[AuthorResult]:
    soup = BeautifulSoup(markup, "lxml")
    results: list[AuthorResult] = []
    seen: set[str] = set()

    for link in soup.select('a[href^="/a/"], a[href^="a/"]'):
        href = link.get("href", "")
        match = re.search(r"/?a/(\d+)$", href)
        if not match:
            continue

        author_id = match.group(1)
        if author_id in seen:
            continue

        name = _clean_text(link.get_text(" ", strip=True))
        if not name or name == "[Все]":
            continue

        results.append(AuthorResult(author_id=author_id, name=name))
        seen.add(author_id)
        if len(results) >= limit:
            break

    return results


def parse_author_page(markup: str, author_id: str, limit: int = 40) -> tuple[str, list[SearchResult]]:
    soup = BeautifulSoup(markup, "lxml")
    author_name = _extract_author_name(soup, author_id)
    books = parse_search_results(markup, limit=limit)
    normalized_books = [
        SearchResult(book_id=item.book_id, title=item.title, author=author_name or item.author)
        for item in books
    ]
    return author_name or f"Автор {author_id}", normalized_books


def parse_book_details(markup: str, base_url: str, book_id: str, page_url: str) -> BookDetails:
    soup = BeautifulSoup(markup, "lxml")
    heading = _extract_book_heading(soup)
    title, heading_author = _split_heading(heading)
    if not title:
        title = f"Книга {book_id}"

    author_refs = _extract_author_refs(soup, heading_author)
    authors = [item.name for item in author_refs] or _extract_authors(soup, heading_author)
    genres = _extract_genres(soup)
    file_size, pages = _extract_book_stats(soup)

    annotation = _extract_annotation(soup)
    formats = _extract_formats(soup, base_url, book_id)

    return BookDetails(
        book_id=book_id,
        title=title,
        authors=_dedupe(authors),
        author_refs=author_refs,
        genres=genres,
        file_size=file_size,
        pages=pages,
        annotation=annotation,
        formats=formats,
        page_url=page_url,
    )


def _extract_author_name(soup: BeautifulSoup, author_id: str) -> str:
    for node in soup.find_all(["h1", "h2"]):
        text = _clean_text(node.get_text(" ", strip=True))
        if text and text.lower() not in {"флибуста", "книги"}:
            return text

    title_node = soup.find("title")
    if title_node:
        title = _clean_text(title_node.get_text(" ", strip=True))
        title = re.sub(r"\s*-\s*Флибуста\s*$", "", title, flags=re.I)
        if title:
            return title

    author_link = soup.select_one(f'a[href="/a/{author_id}"], a[href="a/{author_id}"]')
    if author_link:
        author = _clean_text(author_link.get_text(" ", strip=True))
        if author and author != "[Все]":
            return author

    return ""


def _extract_author_refs(soup: BeautifulSoup, heading_author: str | None) -> list[AuthorResult]:
    if heading_author:
        return [AuthorResult(author_id="", name=heading_author)]

    refs: list[AuthorResult] = []
    seen: set[str] = set()
    main = soup.select_one("#main") or soup.body or soup
    for node in main.descendants:
        if isinstance(node, str) and re.search("Аннотация|Скачать|Жанр", node, re.I):
            break

        if getattr(node, "name", None) != "a":
            continue

        href = node.get("href", "")
        match = re.match(r"/?a/(\d+)", href)
        if not match:
            continue

        author_id = match.group(1)
        if author_id in seen:
            continue

        name = _clean_text(node.get_text(" ", strip=True))
        if not name or name == "[Все]":
            continue

        refs.append(AuthorResult(author_id=author_id, name=name))
        seen.add(author_id)

    return refs


def _extract_formats(soup: BeautifulSoup, base_url: str, book_id: str) -> list[DownloadFormat]:
    known = {
        "fb2": "FB2",
        "epub": "EPUB",
        "mobi": "MOBI",
        "pdf": "PDF",
        "txt": "TXT",
        "rtf": "RTF",
    }
    non_download_codes = {"read", "html", "online", "view"}
    formats: list[DownloadFormat] = []
    seen: set[str] = set()

    for link in soup.select("a[href]"):
        href = link.get("href", "")
        match = re.search(rf"/?b/{re.escape(book_id)}/([a-z0-9]+)(?:\?.*)?$", href, re.I)
        if not match:
            continue

        code = match.group(1).lower()
        if code in non_download_codes or code in seen:
            continue

        text = _clean_text(link.get_text(" ", strip=True)).upper()
        if text in {"ЧИТАТЬ", "READ", "ONLINE"}:
            continue
        label = known.get(code, text or code.upper())
        formats.append(DownloadFormat(code=code, label=label, url=urljoin(base_url + "/", href)))
        seen.add(code)

    return formats


def _extract_genres(soup: BeautifulSoup) -> list[str]:
    genres = [
        _clean_text(link.get_text(" ", strip=True))
        for link in soup.select('a[href^="/g/"], a[href^="g/"]')
        if _clean_text(link.get_text(" ", strip=True))
    ]
    return _dedupe([genre for genre in genres if genre != "[Все]"])


def _extract_book_stats(soup: BeautifulSoup) -> tuple[str | None, int | None]:
    main = soup.select_one("#main") or soup.body or soup
    text = _clean_text(main.get_text(" ", strip=True))
    match = re.search(r"\b(\d+(?:[.,]\d+)?\s*[KMG]B?|\d+(?:[.,]\d+)?\s*[KMG])\s*,\s*(\d+)\s*с\.", text, re.I)
    if match:
        return match.group(1).upper().replace(" ", ""), int(match.group(2))

    pages_match = re.search(r"\b(\d+)\s*с\.", text, re.I)
    pages = int(pages_match.group(1)) if pages_match else None
    return None, pages


def _extract_book_heading(soup: BeautifulSoup) -> str:
    for node in soup.find_all(["h2", "h1"]):
        text = _clean_text(node.get_text(" ", strip=True))
        if text and text.lower() not in {"флибуста", "аннотация"}:
            return text

    title_node = soup.find("title")
    if title_node:
        title = _clean_text(title_node.get_text(" ", strip=True))
        title = re.sub(r"\s*-\s*Флибуста\s*$", "", title, flags=re.I)
        if title and title.lower() != "флибуста":
            return title

    return ""


def _split_heading(heading: str) -> tuple[str, str | None]:
    clean = _clean_text(heading)
    if not clean:
        return "", None

    if ":" not in clean:
        return clean, None

    author, title = clean.split(":", 1)
    author = _clean_text(author)
    title = _clean_text(title)
    if author and title:
        return title, author

    return clean, None


def _extract_authors(soup: BeautifulSoup, heading_author: str | None) -> list[str]:
    if heading_author:
        return [heading_author]

    main = soup.select_one("#main") or soup.body or soup
    authors: list[str] = []

    for node in main.descendants:
        if isinstance(node, str) and re.search("Аннотация|Скачать|Жанр", node, re.I):
            break

        if not getattr(node, "name", None) == "a":
            continue

        href = node.get("href", "")
        if not re.match(r"/?a/\d+", href):
            continue

        author = _clean_text(node.get_text(" ", strip=True))
        if not author or author == "[Все]":
            continue
        authors.append(author)

    return _dedupe(authors)


def _extract_annotation(soup: BeautifulSoup) -> str | None:
    candidates: Iterable[str] = []
    annotation_header = soup.find(string=re.compile("Аннотация", re.I))
    if annotation_header:
        parent = annotation_header.find_parent()
        if parent:
            candidates = [node.get_text(" ", strip=True) for node in parent.find_all_next(["p", "div"], limit=4)]

    for text in candidates:
        clean = _clean_text(text)
        if clean and not clean.lower().startswith("аннотация"):
            return clean[:1500]

    for selector in (".annotation", "#annotation"):
        node = soup.select_one(selector)
        if node:
            clean = _clean_text(node.get_text(" ", strip=True))
            if clean:
                return clean[:1500]

    return None


def _find_nearby_author(link) -> str | None:
    container = link.find_parent(["li", "p", "div", "tr"])
    if not container:
        return None

    author_link = container.select_one('a[href^="/a/"], a[href^="a/"]')
    if not author_link:
        return None

    author = _clean_text(author_link.get_text(" ", strip=True))
    return author or None


def _filename_from_response(response: httpx.Response) -> str | None:
    disposition = response.headers.get("content-disposition")
    if not disposition:
        return None

    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.I)
    if not match:
        return None

    return html.unescape(match.group(1))


def _filename_from_url(url: str) -> str:
    suffix = url.rstrip("/").split("/")[-1]
    return f"book.{suffix or 'bin'}"


def _unzip_fb2_if_needed(
    content: bytes,
    filename: str,
    content_type: str,
    max_bytes: int,
) -> tuple[bytes, str, str]:
    if not zipfile.is_zipfile(BytesIO(content)):
        return content, filename, content_type

    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            fb2_names = [
                item.filename
                for item in archive.infolist()
                if not item.is_dir() and item.filename.lower().endswith(".fb2")
            ]
            if len(fb2_names) != 1:
                logger.debug("Zip archive is not auto-unpacked: filename=%s fb2_files=%s", filename, len(fb2_names))
                return content, filename, content_type

            info = archive.getinfo(fb2_names[0])
            if info.file_size > max_bytes:
                raise FlibustaError("Распакованный FB2 больше лимита Telegram для этого бота.")

            unpacked = archive.read(info)
            unpacked_name = _safe_archive_filename(info.filename) or _zip_filename_to_fb2(filename)
            logger.debug(
                "Unpacked FB2 from zip: archive=%s file=%s size=%s",
                filename,
                unpacked_name,
                len(unpacked),
            )
            return unpacked, unpacked_name, "application/x-fictionbook+xml"
    except zipfile.BadZipFile:
        return content, filename, content_type


def _safe_archive_filename(filename: str) -> str:
    clean = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if clean.lower().endswith(".fb2"):
        return clean
    return ""


def _zip_filename_to_fb2(filename: str) -> str:
    if filename.lower().endswith(".zip"):
        return filename[:-4] + ".fb2"
    return filename + ".fb2"


def _safe_log_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    return parsed._replace(query="...").geturl()


def _int_header(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        result.append(value)
        seen.add(key)
    return result
