"""Small, bounded query operations evaluated against actual catalog metadata."""

from dataclasses import replace

from app.services.author_names import name_tokens, name_word_matches, one_edit_apart
from app.services.search.types import SearchMode
from app.services.search_logic import norm

STOP_WORDS = {
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


def author_queries(query):
    words = norm(query).split()
    variants = [query]
    if len(words) > 1:
        last = words[-1]
        if last.endswith(("ского", "цкого")):
            anchor = last[:-3] + "ий"
        elif last.endswith("ого"):
            anchor = last[:-3] + "ой"
        elif len(last) > 4 and last.endswith(("а", "у", "е")):
            anchor = last[:-1]
        else:
            anchor = last
        variants += (
            [" ".join(words[-2:]), " ".join(words[:2])]
            if len(words) > 3
            else [anchor, words[0]]
        )
    return tuple(dict.fromkeys(variants))[:3]


def probe_worthwhile(query):
    words = norm(query).split()
    return 1 <= len(words) <= 4 and all(
        word.isalpha() and word not in STOP_WORDS for word in words
    )


def split_name(query, author, *, fuzzy=False):
    """Match each query token to at most one catalog-name token."""
    words = norm(query).split()
    available = list(name_tokens(author))
    matched = []
    edited = False
    for index, word in enumerate(words):
        found = next(
            (
                i
                for i, actual in enumerate(available)
                if name_word_matches(word, actual)
            ),
            None,
        )
        if found is None and fuzzy:
            found = next(
                (
                    i
                    for i, actual in enumerate(available)
                    if one_edit_apart(word, actual)
                ),
                None,
            )
            edited = edited or found is not None
        if found is not None:
            matched.append(index)
            available.pop(found)
    if not matched:
        return None
    remaining = " ".join(word for i, word in enumerate(words) if i not in matched)
    return remaining, edited


def title_relevant(query, title, *, fuzzy=True, min_fuzzy_length=5):
    wanted = [w for w in norm(query).split() if w not in STOP_WORDS]
    available = [w for w in norm(title).split() if w not in STOP_WORDS]
    edits = 0
    if not wanted:
        return False
    for word in wanted:
        found = next(
            (
                i
                for i, actual in enumerate(available)
                if word == actual or (len(word) >= 4 and actual.startswith(word))
            ),
            None,
        )
        if found is None and fuzzy and edits == 0:
            found = next(
                (
                    i
                    for i, actual in enumerate(available)
                    if min(len(word), len(actual)) >= min_fuzzy_length
                    and one_edit_apart(word, actual)
                ),
                None,
            )
            if found is not None:
                edits += 1
        if found is None:
            return False
        available.pop(found)
    return True


def author_plan(plan, author):
    return replace(
        plan, mode=SearchMode.AUTHOR, author=author, title=None, catalog_lookup=False
    )


def combined_plan(plan, author, title):
    return replace(
        plan,
        mode=SearchMode.AUTHOR_TITLE,
        author=author,
        title=title,
        primary_queries=(title,),
        fallback_queries=(),
        catalog_lookup=False,
    )


def book_evidence(plan, books):
    combinations = {}
    for book in books:
        if not book.author:
            continue
        split = split_name(plan.cleaned_query, book.author)
        if split and split[0] and title_relevant(split[0], book.title):
            combinations.setdefault((book.author, split[0]), []).append(book)
    return combinations


def correction(query, books, authors):
    choices = {}
    for author in authors:
        split = split_name(query, author.name, fuzzy=True)
        if split and not split[0] and split[1]:
            choices.setdefault(norm(author.name), author.name)
    for book in books:
        if title_relevant(query, book.title, min_fuzzy_length=3) and norm(
            query
        ) != norm(book.title):
            choices.setdefault(norm(book.title), book.title)
        if book.author:
            split = split_name(query, book.author, fuzzy=True)
            if split and split[1]:
                proposed = (
                    book.author if not split[0] else f"{book.author} — {book.title}"
                )
                if not split[0] or title_relevant(split[0], book.title):
                    choices.setdefault(norm(proposed), proposed)
    return next(iter(choices.values())) if len(choices) == 1 else None
