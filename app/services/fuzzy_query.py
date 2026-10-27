"""Bounded lexical matching against catalog candidates, never invented identities."""

from itertools import permutations
from collections import Counter
import re

MAX_NAMES = 2048
MAX_SCORED_NAMES = 64
FUNCTION_WORDS = {
    "и",
    "в",
    "во",
    "на",
    "о",
    "об",
    "с",
    "со",
    "из",
    "для",
    "по",
    "про",
    "к",
    "от",
    "без",
    "под",
}
REQUEST_WORDS = {"книга", "книгу", "книги", "роман", "автор", "автора"}


def tokens(text):
    return re.findall(r"[a-zа-я0-9]+", text.casefold().replace("ё", "е"))


def edit_distance(left, right):
    """Optimal string alignment distance: insert/delete/replace/adjacent swap."""
    if left == right:
        return 0
    if max(len(left), len(right)) > 64:
        return max(len(left), len(right))
    previous = list(range(len(right) + 1))
    older = None
    for i, a in enumerate(left, 1):
        current = [i] + [0] * len(right)
        for j, b in enumerate(right, 1):
            current[j] = min(
                previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)
            )
            if older is not None and j > 1 and a == right[j - 2] and left[i - 2] == b:
                current[j] = min(current[j], older[j - 2] + 1)
        older, previous = previous, current
    return previous[-1]


def _grams(text):
    value = "".join(sorted(tokens(text)))
    return {value[i : i + 2] for i in range(max(1, len(value) - 1))}


def _name_score(query, name):
    wanted, actual = tokens(query), tokens(name)
    if (
        not wanted
        or len(wanted) > 4
        or not any(len(w) >= 3 for w in wanted)
        or not all(w.isalpha() for w in wanted)
    ):
        return None
    if len(actual) > len(wanted):
        actual = [
            w
            for w in actual
            if not (
                len(w) >= 5 and re.search(r"(?:ович|евич|ильич|овна|евна|ична)$", w)
            )
        ]
    if len(wanted) == 1:
        options = []
        for word in actual:
            length = max(len(wanted[0]), len(word))
            if min(len(wanted[0]), len(word)) < 3:
                continue
            score = 1 - edit_distance(wanted[0], word) / length
            if score >= (0.75 if length <= 4 else 0.8):
                options.append(score)
        return max(options) if options else None
    if len(wanted) != len(actual) or len(actual) > 4:
        return None
    costs = [
        [
            0
            if len(a) == 1 and len(b) >= 3 and b.startswith(a)
            else edit_distance(a, b)
            for b in actual
        ]
        for a in wanted
    ]
    best = None
    for order in permutations(range(len(actual))):
        lengths = [
            1
            if len(a) == 1 and len(actual[j]) >= 3 and actual[j].startswith(a)
            else max(len(a), len(actual[j]))
            for a, j in zip(wanted, order)
        ]
        distances = [costs[i][j] for i, j in enumerate(order)]
        if any(
            (
                min(len(a), len(actual[j])) < 3
                and not (len(a) == 1 and actual[j].startswith(a))
            )
            or 1 - d / max(length, 1) < 0.63
            for a, j, d, length in zip(wanted, order, distances, lengths)
        ):
            continue
        score = 1 - sum(distances) / sum(lengths)
        if score >= 0.72:
            best = max(best or 0, score)
    return best


def author_choices(query, names):
    """Unique close name or up to three near-tied choices; exact names aren't fixes."""
    grams = _grams(query)
    candidates = []
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name or len(name) > 120:
            continue
        key = tuple(sorted(tokens(name)))
        if not key or key in seen:
            continue
        if len(seen) >= MAX_NAMES:
            break
        seen.add(key)
        other = _grams(name)
        overlap = 2 * len(grams & other) / max(1, len(grams) + len(other))
        if overlap >= 0.2:
            candidates.append((overlap, name))
        if len(candidates) >= MAX_NAMES:
            break
    scored = []
    for _, name in sorted(candidates, key=lambda item: (-item[0], item[1]))[
        :MAX_SCORED_NAMES
    ]:
        score = _name_score(query, name)
        if score is not None:
            scored.append((score, name))
    if not scored:
        return ()
    # A surname-only candidate adds no choice when complete names are present.
    # Likewise prefer a complete byline over its duplicate without patronymic,
    # while keeping two conflicting complete bylines distinct.
    filtered = []
    for score, name in scored:
        words = tokens(name)
        redundant = False
        for _, other in scored:
            parts = tokens(other)
            if len(words) == 1 and len(parts) > 1 and words[0] in parts:
                redundant = True
                break
            core = [
                w
                for w in parts
                if not (
                    len(w) >= 5 and re.search(r"(?:ович|евич|ильич|овна|евна|ична)$", w)
                )
            ]
            if len(words) == 2 and len(parts) > 2 and sorted(core) == sorted(words):
                redundant = True
                break
        if not redundant:
            filtered.append((score, name))
    scored = filtered or scored
    scored.sort(key=lambda item: (-item[0], item[1].casefold()))
    query_key = tuple(sorted(tokens(query)))
    if scored[0][0]>=1 and (not any(len(word)==1 for word in tokens(query)) or
            any(score>=1 and tuple(sorted(tokens(name)))==query_key for score,name in scored)):
        return ()
    cutoff = scored[0][0] - 0.07
    return tuple(name for score, name in scored if score >= cutoff)[:3]


def full_query_supported(query, title, author=None):
    """Every meaningful query token needs its own title/author evidence."""
    wanted = tokens(query)
    if len(wanted) > 1:
        wanted = [w for w in wanted if w not in FUNCTION_WORDS | REQUEST_WORDS]
    if not wanted:
        return False
    title_words = tokens(title)
    if Counter(wanted) <= Counter([*title_words, *tokens(author or "")]) and set(
        wanted
    ) & set(title_words):
        return True
    if len(wanted) > 12:
        return False
    available = [(w, True) for w in title_words if w not in FUNCTION_WORDS]
    available += [(w, False) for w in tokens(author or "")]
    pairs = []
    for i, word in enumerate(wanted):
        for j, (actual, is_title) in enumerate(available[:64]):
            if word == actual:
                score = 1.0
            elif len(word) >= 4 and actual.startswith(word):
                score = 0.95
            elif (
                min(len(word), len(actual)) >= (5 if is_title else 3)
                and word.isalpha()
                and actual.isalpha()
            ):
                score = 1 - edit_distance(word, actual) / max(len(word), len(actual))
                if score < (0.75 if is_title else 0.63):
                    continue
            else:
                continue
            pairs.append((score, i, j, is_title))
    matched_query, matched_actual = set(), set()
    title_evidence = False
    for score, i, j, is_title in sorted(
        pairs, key=lambda p: (p[0], p[3]), reverse=True
    ):
        if i not in matched_query and j not in matched_actual:
            matched_query.add(i)
            matched_actual.add(j)
            title_evidence |= is_title
    return len(matched_query) == len(wanted) and title_evidence
