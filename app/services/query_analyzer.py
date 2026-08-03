from __future__ import annotations

from dataclasses import dataclass
import re


FORMAT_HINTS = {"epub", "fb2", "pdf", "mobi", "txt"}


@dataclass(frozen=True)
class QueryAnalysis:
    original: str
    cleaned: str
    quoted_title: bool
    likely_author: bool
    format_hint: str | None
    author_part: str | None
    title_part: str | None
    has_year_or_series: bool


def analyze_query(query: str, max_words: int = 12) -> QueryAnalysis:
    original = query.strip()
    quoted = bool(re.search(r'["«“][^"»”]+["»”]', original))
    format_hint: str | None = None
    kept: list[str] = []

    for word in original.split()[:max_words]:
        bare = re.sub(r"[^A-Za-zА-Яа-я0-9]+", "", word).lower()
        if bare in FORMAT_HINTS and format_hint is None:
            format_hint = bare
            continue
        kept.append(word)

    cleaned = re.sub(r"\s+", " ", " ".join(kept)).strip()
    author_part: str | None = None
    title_part: str | None = None
    for separator in (" - ", " — ", " – ", ": "):
        if separator not in cleaned:
            continue
        left, right = cleaned.split(separator, 1)
        if _looks_person(left):
            author_part, title_part = left.strip(), right.strip()
        elif _looks_person(right):
            author_part, title_part = right.strip(), left.strip()
        break

    if author_part is None and title_part is None and len(kept) >= 3:
        possible_author = " ".join(kept[:2])
        if _looks_person(possible_author):
            author_part, title_part = possible_author, " ".join(kept[2:])

    return QueryAnalysis(
        original=original,
        cleaned=cleaned,
        quoted_title=quoted,
        likely_author=_looks_person(cleaned) and not quoted,
        format_hint=format_hint,
        author_part=author_part,
        title_part=title_part,
        has_year_or_series=bool(
            re.search(r"\b(?:18|19|20)\d{2}\b|#\d+|\bкн\.?\s*\d+", cleaned, re.I)
        ),
    )


def _looks_person(text: str) -> bool:
    parts = [part for part in re.split(r"\s+", text.strip()) if part]
    return 2 <= len(parts) <= 4 and all(
        re.fullmatch(r"[A-Za-zА-Яа-яЁё-]+", part) for part in parts
    )
