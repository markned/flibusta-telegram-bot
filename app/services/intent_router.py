from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from app.services.query_analyzer import analyze_query
from app.services.author_names import (KNOWN_AUTHOR_NAMES, canonical_author_query, is_known_author_query, known_surnames, name_tokens, author_query_choices)
from app.services.search_logic import clean_query, norm


class IntentKind(str, Enum):
    EXACT_SEARCH = "exact_search"
    AUTHOR_SEARCH = "author_search"
    AUTHOR_TITLE_SEARCH = "author_title_search"
    UNSUPPORTED_TOPIC = "unsupported_topic"
    UNKNOWN_FALLBACK = "unknown_fallback"


@dataclass(frozen=True)
class IntentDecision:
    kind: IntentKind
    confidence: float
    original_query: str
    cleaned_query: str
    search_query: str | None
    author_part: str | None
    title_part: str | None
    format_hint: str | None
    reasons: list[str]


BROAD_QUERY_PATTERNS = (
    r"\bподбери\w*",
    r"\bпосовет\w*",
    r"\bпорекомендуй\w*",
    r"\bчто\s+почитать",
    r"\bхочу\s+почитать",
    r"\bинтересн\w*\s+(?:о|об|про)\b",
    r"\b(?:что нибудь|что то|какое нибудь)\b",
    r"\bпокороче\b",
    r"\bчто-то\s+похож",
    r"\bпохож\w*\s+на",
    r"\bв\s+духе",
    r"\bподборка\s+\w",
    r"\bкниг[аи]?\s+(?:о|об|про|как)",
)
BROAD_TOPIC_STEMS = (
    "антиутоп",
    "киберпанк",
    "постмодерн",
    "попадан",
    "литрпг",
    "бояр",
    "хоррор",
    "ужас",
    "фэнтези",
    "магическ",
    "детектив",
    "триллер",
    "фантастик",
)
KNOWN_SURNAMES = {
    "толстой",
    "достоевский",
    "пелевин",
    "сорокин",
    "булгаков",
    "оруэлл",
    "хаксли",
    "замятин",
    "патту",
    "остер",
    "муравьев",
    "муравьёв",
    "мураками",
    "герберт",
}
FIRST_NAMES = {
    "эдит",
    "лев",
    "федор",
    "фёдор",
    "михаил",
    "джордж",
    "виктор",
    "харуки",
    "пол",
    "томас",
    "петр",
    "пётр",
    "константин",
    "владимир",
    "аркадий",
    "антон",
    "фрэнк",
}


def route_intent(query: str) -> IntentDecision:
    analysis = analyze_query(query, max_words=24)
    cleaned = clean_query(analysis.cleaned or query)
    normalized = norm(cleaned)
    reasons: list[str] = []

    # Quotation marks explicitly force a literal catalog search, even when a
    # title happens to look like a genre or a broad topic.
    if analysis.quoted_title:
        return _decision(
            IntentKind.EXACT_SEARCH,
            0.98,
            query,
            cleaned,
            cleaned,
            None,
            None,
            analysis.format_hint,
            ["quoted_title"],
        )

    broad = any(re.search(pattern, normalized, re.I) for pattern in BROAD_QUERY_PATTERNS)
    broad = broad or any(stem in normalized for stem in BROAD_TOPIC_STEMS)
    if normalized.startswith("подборка ") and len(cleaned.split()) <= 3 and not any(
        marker in normalized for marker in ("как ", "русск", "хорош", "лучшие", "постмодерн")
    ):
        broad = False
        reasons.append("title_like_podborka")
    if broad:
        return _decision(
            IntentKind.UNSUPPORTED_TOPIC,
            0.96,
            query,
            cleaned,
            None,
            None,
            None,
            analysis.format_hint,
            ["broad_topic_not_supported"],
        )

    author_prefix = re.match(r"^(?:книги|произведения)(?:\s+автора)?\s+(.+)$", cleaned, re.I)
    author_query = author_prefix.group(1) if author_prefix else cleaned
    canonical = canonical_author_query(author_query)
    single_surname = len(author_query.split()) == 1 and canonical in (KNOWN_SURNAMES | known_surnames())
    typo = author_query_choices(author_query)
    if ((author_prefix and 1 <= len(author_query.split()) <= 4)
            or is_known_author_query(author_query) or single_surname or typo):
        return _decision(
            IntentKind.AUTHOR_SEARCH, 0.95 if not typo else 0.65,
            query, cleaned, canonical, canonical, None, analysis.format_hint,
            ["explicit_author" if author_prefix else "author_identity" if not typo else "author_typo"],
        )

    detected = detect_author_title_query(cleaned)
    if detected:
        author, title = detected
        return _decision(
            IntentKind.AUTHOR_TITLE_SEARCH,
            0.88,
            query,
            cleaned,
            cleaned,
            author,
            title,
            analysis.format_hint,
            ["author_title_heuristic"],
        )
    if analysis.likely_author and cleaned.split()[0].lower() in FIRST_NAMES:
        return _decision(
            IntentKind.AUTHOR_SEARCH,
            0.86,
            query,
            cleaned,
            cleaned,
            canonical_author_query(cleaned),
            None,
            analysis.format_hint,
            ["person_name"],
        )
    if len(cleaned.split()) <= 6:
        return _decision(
            IntentKind.EXACT_SEARCH,
            0.72,
            query,
            cleaned,
            cleaned,
            None,
            None,
            analysis.format_hint,
            reasons or ["title_like"],
        )
    return _decision(
        IntentKind.UNKNOWN_FALLBACK,
        0.4,
        query,
        cleaned,
        cleaned,
        None,
        None,
        analysis.format_hint,
        ["fallback"],
    )


def detect_author_title_query(query: str) -> tuple[str, str] | None:
    text = clean_query(query)
    words = text.split()
    lowered = [word.lower() for word in words]
    for separator in (" - ", " — ", " – ", ": "):
        if separator not in text:
            continue
        left, right = (part.strip() for part in text.split(separator, 1))
        if not left or not right:
            return None
        if right.split()[-1].casefold() in KNOWN_SURNAMES:
            return right, left
        if _looks_explicit_author(left):
            return left, right
        if _looks_surname(left.split()[-1].lower()):
            return left.strip(), right.strip()
        if _looks_surname(right.split()[-1].lower()):
            return right.strip(), left.strip()
        return None

    if not 2 <= len(words) <= 6:
        return None
    if len(words) >= 3 and lowered[-2] in {"в", "с", "со", "на", "о", "об", "от", "для", "из", "к", "по", "без", "под"}:
        return None
    if _looks_person_name_only(lowered):
        return None
    known = _known_author_at_edge(words, lowered)
    if known:
        return known
    if lowered[-1] in (KNOWN_SURNAMES | known_surnames()):
        return words[-1], " ".join(words[:-1])
    if lowered[0] in KNOWN_SURNAMES:
        return words[0], " ".join(words[1:])
    return None


def _looks_explicit_author(text: str) -> bool:
    parts = text.split()
    return 2 <= len(parts) <= 3 and all(
        part.casefold() not in {"и", "в", "на", "с", "со", "о", "об", "из", "для", "по", "без", "к"}
        and re.fullmatch(r"[A-Za-zА-Яа-яЁё-]+", part)
        for part in parts
    )


def _known_author_at_edge(words: list[str], lowered: list[str]) -> tuple[str, str] | None:
    for size in (3, 2):
        if len(words) <= size:
            continue
        start = name_tokens(" ".join(words[:size]))
        if start in KNOWN_AUTHOR_NAMES:
            return KNOWN_AUTHOR_NAMES[start], " ".join(words[size:])
        end = name_tokens(" ".join(words[-size:]))
        if end in KNOWN_AUTHOR_NAMES:
            return KNOWN_AUTHOR_NAMES[end], " ".join(words[:-size])
    return None


def _looks_person_name_only(lowered: list[str]) -> bool:
    if len(lowered) == 2 and lowered[0] in FIRST_NAMES and _looks_surname(lowered[1]):
        return True
    return bool(
        len(lowered) == 3
        and lowered[0] in FIRST_NAMES
        and _looks_patronymic(lowered[1])
        and _looks_surname(lowered[2])
    )


def _looks_surname(word: str) -> bool:
    return word in KNOWN_SURNAMES or any(
        word.endswith(suffix) for suffix in ("ов", "ев", "ёв", "ин", "ын", "ский", "цкий", "ой", "ая")
    )


def _looks_patronymic(word: str) -> bool:
    return word.endswith(("вич", "вна", "ична", "ович", "евич", "инична"))


def _decision(
    kind: IntentKind,
    confidence: float,
    original: str,
    cleaned: str,
    search_query: str | None,
    author: str | None,
    title: str | None,
    format_hint: str | None,
    reasons: list[str],
) -> IntentDecision:
    return IntentDecision(
        kind=kind,
        confidence=confidence,
        original_query=original,
        cleaned_query=cleaned,
        search_query=search_query,
        author_part=author,
        title_part=title,
        format_hint=format_hint,
        reasons=reasons,
    )
