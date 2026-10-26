"""Small author aliases and conservative name matching; not a morphology engine.

The catalog supplies all IDs/results. Known names are routing hints only, and
one-edit alternatives always require confirmation rather than silently correcting.
"""

from __future__ import annotations

import re

from app.services.search_logic import norm

KNOWN_AUTHOR_NAMES = {
    ("агата", "кристи"): "Агата Кристи",
    ("алексей", "толстой"): "Алексей Толстой",
    ("антон", "чехов"): "Антон Чехов",
    ("аркадий", "стругацкий"): "Аркадий Стругацкий",
    ("виктор", "пелевин"): "Виктор Пелевин",
    ("владимир", "сорокин"): "Владимир Сорокин",
    ("джейн", "остин"): "Джейн Остин",
    ("джордж", "оруэлл"): "Джордж Оруэлл",
    ("евгений", "замятин"): "Евгений Замятин",
    ("лев", "николаевич", "толстой"): "Лев Толстой",
    ("лев", "толстой"): "Лев Толстой",
    ("михаил", "булгаков"): "Михаил Булгаков",
    ("олдос", "хаксли"): "Олдос Хаксли",
    ("пол", "остер"): "Пол Остер",
    ("стивен", "кинг"): "Стивен Кинг",
    ("федор", "достоевский"): "Фёдор Достоевский",
    ("федор", "михайлович", "достоевский"): "Фёдор Достоевский",
    ("фрэнк", "герберт"): "Фрэнк Герберт",
    ("фёдор", "достоевский"): "Фёдор Достоевский",
    ("фёдор", "михайлович", "достоевский"): "Фёдор Достоевский",
    ("харуки", "мураками"): "Харуки Мураками",
    ("эдит", "патту"): "Эдит Патту",
}


def _genitive(word: str, *, surname=False) -> str:
    if word == "лев":
        return "льва"
    if surname and word.endswith(("ский", "цкий")):
        return word[:-2] + "ого"
    if surname and word.endswith("ой"):
        return word[:-2] + "ого"
    if word.endswith("й"):
        return word[:-1] + "я"
    if word.endswith("а"):
        return word[:-1] + ("и" if word[-2:-1] in "гкхжчшщ" else "ы")
    if word.endswith("я"):
        return word[:-1] + "и"
    if word[-1:] not in "аеёиоуыэюяь":
        return word + "а"
    return word


# Only known tokens have inflected aliases; never strip suffixes from arbitrary
# titles or map a surname to one particular author with that surname.
_ALIASES: dict[str, str] = {}
for _parts in KNOWN_AUTHOR_NAMES:
    for _index, _part in enumerate(_parts):
        _word = norm(_part)
        _ALIASES[_word] = _word
        _ALIASES[_genitive(_word, surname=_index == len(_parts) - 1)] = _word


def name_tokens(text: str) -> tuple[str, ...]:
    return tuple(_ALIASES.get(word, word) for word in norm(text).split())


def canonical_author_query(text: str) -> str:
    tokens = name_tokens(text)
    if len(tokens) > 2 and tokens in KNOWN_AUTHOR_NAMES:
        return " ".join(word.capitalize() for word in tokens)
    return KNOWN_AUTHOR_NAMES.get(tokens, " ".join(tokens))


def is_known_author_query(text: str) -> bool:
    return name_tokens(text) in KNOWN_AUTHOR_NAMES


def known_surnames() -> set[str]:
    return {norm(parts[-1]) for parts in KNOWN_AUTHOR_NAMES}


def name_word_matches(expected: str, actual: str) -> bool:
    """One-way inflections of a catalog token, never arbitrary prefix stemming."""
    expected = name_tokens(expected)
    actual = name_tokens(actual)
    if len(expected) != 1 or len(actual) != 1:
        return False
    wanted, word = expected[0], actual[0]
    forms = {word, _genitive(word, surname=True), _genitive(word)}
    if word.endswith(("ский", "цкий", "ой")):
        forms.update(word[:-2] + ending for ending in ("ого", "ому", "ым", "ом"))
    elif word.endswith("а"):
        forms.update(word[:-1] + ending for ending in ("ы", "и", "е", "у", "ой"))
    elif word.endswith("я"):
        forms.update(word[:-1] + ending for ending in ("и", "е", "ю", "ей"))
    elif word[-1:] not in "аеёиоуыэюяьй":
        forms.update(word + ending for ending in ("а", "у", "ом", "е"))
    return wanted in forms


def names_match(expected: str, actual: str) -> bool:
    wanted = set(name_tokens(expected))
    if not wanted:
        return False

    def patronymics(tokens):
        return (
            {word for word in tokens if word.endswith(("вич", "вна", "ична"))}
            if len(tokens) >= 3
            else set()
        )

    # A missing patronymic is a normal abbreviated byline; conflicting explicit
    # patronymics or given names are not interchangeable.
    expected_middle = patronymics(wanted)
    for candidate in re.split(r"[,;]|\s+и\s+", actual):
        available = set(name_tokens(candidate))
        actual_middle = patronymics(available)
        if expected_middle and actual_middle and expected_middle != actual_middle:
            continue
        remaining = list(available - actual_middle)
        for word in wanted - expected_middle:
            found = next((i for i, actual_word in enumerate(remaining) if name_word_matches(word, actual_word)), None)
            if found is None:
                break
            remaining.pop(found)
        else:
            return True
    return False


def one_edit_apart(left: str, right: str) -> bool:
    if (
        left == right
        or min(len(left), len(right)) < 3
        or abs(len(left) - len(right)) > 1
    ):
        return False
    if len(left) == len(right):
        positions = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
        return len(positions) == 1 or (
            len(positions) == 2
            and positions[1] == positions[0] + 1
            and left[positions[0]] == right[positions[1]]
            and left[positions[1]] == right[positions[0]]
        )
    shorter, longer = sorted((left, right), key=len)
    return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))


def suggest_author_query(query: str, candidates=()) -> str | None:
    wanted = name_tokens(query)
    names = list(candidates) + list(dict.fromkeys(KNOWN_AUTHOR_NAMES.values()))
    if len(wanted) == 1:
        names += sorted(known_surnames())
    plausible = {}
    for name in names:
        actual = name_tokens(name)
        if len(wanted) != len(actual):
            continue
        differences = [(a, b) for a, b in zip(wanted, actual) if a != b]
        if len(differences) == 1 and one_edit_apart(*differences[0]):
            plausible.setdefault(actual, name)
    return next(iter(plausible.values())) if len(plausible) == 1 else None
