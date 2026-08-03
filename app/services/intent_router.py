from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from app.services.query_analyzer import analyze_query
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
KNOWN_AUTHOR_NAMES = {
    ("лев", "толстой"): "Лев Толстой",
    ("лев", "николаевич", "толстой"): "Лев Толстой",
    ("федор", "достоевский"): "Фёдор Достоевский",
    ("фёдор", "достоевский"): "Фёдор Достоевский",
    ("федор", "михайлович", "достоевский"): "Фёдор Достоевский",
    ("фёдор", "михайлович", "достоевский"): "Фёдор Достоевский",
    ("михаил", "булгаков"): "Михаил Булгаков",
    ("виктор", "пелевин"): "Виктор Пелевин",
    ("владимир", "сорокин"): "Владимир Сорокин",
    ("джордж", "оруэлл"): "Джордж Оруэлл",
    ("олдос", "хаксли"): "Олдос Хаксли",
    ("евгений", "замятин"): "Евгений Замятин",
    ("эдит", "патту"): "Эдит Патту",
    ("пол", "остер"): "Пол Остер",
    ("харуки", "мураками"): "Харуки Мураками",
    ("аркадий", "стругацкий"): "Аркадий Стругацкий",
    ("антон", "чехов"): "Антон Чехов",
    ("фрэнк", "герберт"): "Фрэнк Герберт",
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
    analysis = analyze_query(query)
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
            None,
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
    if not 2 <= len(words) <= 6:
        return None

    for separator in (" - ", " — ", " – ", ": "):
        if separator not in text:
            continue
        left, right = text.split(separator, 1)
        if _looks_surname(left.split()[-1].lower()):
            return left.strip(), right.strip()
        if _looks_surname(right.split()[-1].lower()):
            return right.strip(), left.strip()
        return None

    if _looks_person_name_only(lowered):
        return None
    known = _known_author_at_edge(words, lowered)
    if known:
        return known
    if _looks_surname(lowered[-1]):
        return words[-1], " ".join(words[:-1])
    if lowered[0] in KNOWN_SURNAMES:
        return words[0], " ".join(words[1:])
    return None


def _known_author_at_edge(words: list[str], lowered: list[str]) -> tuple[str, str] | None:
    for size in (3, 2):
        if len(words) <= size:
            continue
        start = tuple(lowered[:size])
        if start in KNOWN_AUTHOR_NAMES:
            return KNOWN_AUTHOR_NAMES[start], " ".join(words[size:])
        end = tuple(lowered[-size:])
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
