from __future__ import annotations

import re

from app.services.intent_router import IntentKind, route_intent
from app.services.search.types import SearchMode, SearchPlan
from app.services.search_logic import clean_query


_EN_TO_RU_KEYBOARD = str.maketrans(
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.",
    "йцукенгшщзхъфывапролджэячсмитьбю",
)


def build_search_plan(query: str, *, max_fallback_queries: int = 2) -> SearchPlan:
    decision = route_intent(query)
    cleaned = clean_query(decision.cleaned_query or query)
    mode = {
        IntentKind.EXACT_SEARCH: SearchMode.EXACT,
        IntentKind.AUTHOR_SEARCH: SearchMode.AUTHOR,
        IntentKind.AUTHOR_TITLE_SEARCH: SearchMode.AUTHOR_TITLE,
        IntentKind.UNSUPPORTED_TOPIC: SearchMode.UNSUPPORTED_TOPIC,
        IntentKind.UNKNOWN_FALLBACK: SearchMode.FALLBACK,
    }[decision.kind]

    primary = [cleaned]
    keyboard_variant = correct_keyboard_layout(cleaned)
    if keyboard_variant and keyboard_variant.casefold() != cleaned.casefold():
        primary.append(keyboard_variant)

    fallback: list[str] = []
    if mode in {SearchMode.EXACT, SearchMode.FALLBACK}:
        words = cleaned.split()
        if len(words) >= 2 and words[0].casefold() in {"книга", "роман", "повесть"}:
            fallback.append(" ".join(words[1:]))
        if len(words) >= 4:
            fallback.extend((" ".join(words[:-1]), " ".join(words[1:])))
    fallback = _dedupe(fallback)[: max(0, max_fallback_queries)]

    return SearchPlan(
        original_query=query,
        cleaned_query=cleaned,
        mode=mode,
        primary_queries=tuple(_dedupe(primary)),
        fallback_queries=tuple(fallback),
        author=decision.author_part,
        title=decision.title_part,
        format_hint=decision.format_hint,
    )


def correct_keyboard_layout(query: str) -> str | None:
    if re.search(r"[а-яё]", query, re.I) or not re.search(r"[a-z]", query, re.I):
        return None
    corrected = query.lower().translate(_EN_TO_RU_KEYBOARD)
    return clean_query(corrected)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result
