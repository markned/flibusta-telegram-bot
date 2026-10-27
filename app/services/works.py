"""Conservative work identities; every edition retains its actual catalog ID."""

from dataclasses import dataclass
import re

from app.flibusta import EditionRef, SearchResult
from app.services.search_logic import norm

VOLUME = re.compile(
    r"(?:^|[\s.,:;—–-]+)(?:том|т\.|книга|част[ьи])\s*([0-9]+|[IVXLCDM]+)(?=$|[\s.,:;—–-])",
    re.I,
)
META = re.compile(
    r"litres|литрес|иллюстр|издани|редакци|перевод|пер\.|исправл|полны[йм]|полное|сокращ|фрагмент|отрывок|ознаком|адаптир|epub|fb2|^си$",
    re.I,
)
PARTIAL = re.compile(
    r"сокращ|фрагмент|отрывок|ознаком|адаптир|пересказ|первый вариант романа", re.I
)


def identity(text):
    return re.sub(
        r"\s+", " ", re.sub(r"[^a-zа-я0-9]+", " ", text.casefold().replace("ё", "е"))
    ).strip()


def work_title(title):
    def annotation(match):
        value = match.group(0)[1:-1]
        return (
            ""
            if META.search(value)
            or (
                match.group(0).startswith("[")
                and re.fullmatch(r"[a-zA-Z0-9\s.,:;!?–—\-\x27]+", value)
            )
            else match.group(0)
        )

    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", annotation, title)
    title = re.sub(
        r"[.\s—–-]+(?:коллекционное\s+)?иллюстрированное\s+издание\s*$",
        "",
        title,
        flags=re.I,
    )
    leading = re.match(
        r"^(?:том|т\.|книга|часть)\s*([0-9]+|[IVXLCDM]+)[.,:;—–-]+\s*(.+)$", title, re.I
    )
    if leading:
        title = leading.group(2)
    title = re.sub(r"[.\s—–-]+первый вариант романа\s*$", "", title, flags=re.I)
    match = VOLUME.search(title)
    if match:
        title = title[: match.start()]
    return re.sub(r"\s+", " ", title).strip(" .,:;—–-")


def _number(value):
    if value.isdigit():
        return int(value)
    roman = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    result, previous = 0, 0
    for letter in value.upper()[::-1]:
        number = roman.get(letter, 0)
        result += -number if number < previous else number
        previous = max(previous, number)
    return result


def edition_order(edition):
    match = VOLUME.search(edition.title)
    if not match:
        return (0, 0, 0)
    number = _number(match.group(1))
    part = re.search(r"част[ьи]\s*(\d+)", edition.title[match.end() :], re.I)
    return (1, number, int(part.group(1)) if part else 0)


def editions(book):
    return book.editions or (EditionRef(book.book_id, book.title, book.author),)


def choose_default(book):
    candidates = [
        e
        for e in editions(book)
        if not VOLUME.search(e.title)
        and not PARTIAL.search(e.title)
        and identity(work_title(e.title)) == identity(book.title)
    ]
    return (
        min(candidates, key=lambda e: (norm(e.title) != norm(book.title), len(e.title)))
        if candidates
        else None
    )


def group_books(books, *, alphabetical=False):
    groups = {}
    for book in books:
        for edition in editions(book):
            title = work_title(edition.title) or edition.title
            # Missing authors cannot establish the identity of a work.
            key = (
                identity(title),
                norm(edition.author or "") or "unknown:" + edition.book_id,
            )
            group = groups.setdefault(
                key,
                {
                    "title": title,
                    "author": edition.author,
                    "editions": {},
                    "source_author_id": book.source_author_id,
                },
            )
            group["editions"].setdefault(edition.book_id, edition)
    result = []
    for group in groups.values():
        refs = tuple(group["editions"].values())
        work = SearchResult(
            refs[0].book_id,
            group["title"],
            group["author"],
            refs,
            group["source_author_id"],
        )
        default = choose_default(work)
        representative = default or refs[0]
        if len(refs) == 1 and refs[0].title == group["title"]:
            work = SearchResult(
                representative.book_id,
                group["title"],
                group["author"],
                source_author_id=group["source_author_id"],
            )
        else:
            work = SearchResult(
                representative.book_id,
                group["title"],
                group["author"],
                refs,
                group["source_author_id"],
            )
        result.append(work)
    return sorted(result, key=lambda b: identity(b.title)) if alphabetical else result


@dataclass(frozen=True)
class AuthorWorksPage:
    name: str
    books: list[SearchResult]
    total: int
    next_offset: int
    has_more: bool
