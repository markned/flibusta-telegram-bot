from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Protocol

from app.services.search.context import SearchStatus, search_status
from app.services.author_names import canonical_author_query, names_match, suggest_author_query

from app.flibusta import AuthorResult, FlibustaError, SearchResult
from app.services.search.planner import build_search_plan
from app.services.search.types import SearchMode, SearchOutcome, SearchPlan
from app.services.search_logic import (
    base_title,
    is_strong_book_match,
    norm,
    rank_and_dedupe_books,
    rank_authors,
)


class SearchClient(Protocol):
    async def search(self, query: str, limit: int = 8) -> list[SearchResult]: ...
    async def search_all(
        self, query: str, book_limit: int = 8, author_limit: int = 20
    ) -> tuple[list[SearchResult], list[AuthorResult]]: ...
    async def search_authors(self, query: str, limit: int = 20) -> list[AuthorResult]: ...
    async def author_books(self, author_id: str, limit: int = 40) -> tuple[str, list[SearchResult]]: ...


@dataclass
class _SearchRun:
    expanded: bool = False
    books: list[SearchResult] = field(default_factory=list)
    authors: list[AuthorResult] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    responses: dict = field(default_factory=dict)


_search_run: ContextVar[_SearchRun | None] = ContextVar("search_run", default=None)


class SearchService:
    def __init__(
        self,
        client: SearchClient,
        *,
        book_limit: int,
        author_limit: int,
        timeout_seconds: float = 12,
        max_fallback_queries: int = 2,
    ) -> None:
        self.client = client
        self.book_limit = book_limit
        self.author_limit = author_limit
        self.timeout_seconds = timeout_seconds
        self.max_fallback_queries = max_fallback_queries

    async def search(self, query: str, *, expanded: bool = False) -> SearchOutcome:
        plan = build_search_plan(query, max_fallback_queries=self.max_fallback_queries)
        if plan.mode == SearchMode.UNSUPPORTED_TOPIC:
            return SearchOutcome(plan, [], [], ())
        if plan.mode == SearchMode.AUTHOR_TITLE:
            return await self._within_deadline(self._search_author_title_with_fallback(plan), plan, expanded=expanded)
        if plan.mode == SearchMode.AUTHOR:
            return await self._within_deadline(self._search_author_intent(plan), plan, expanded=expanded)
        if plan.mode == SearchMode.EXACT:
            return await self._within_deadline(self._search_exact(plan), plan, expanded=expanded)
        return await self._within_deadline(self._search_general(plan), plan, expanded=expanded)

    async def search_books(self, query: str) -> SearchOutcome:
        plan = build_search_plan(query, max_fallback_queries=self.max_fallback_queries)
        plan = SearchPlan(
            original_query=plan.original_query,
            cleaned_query=plan.cleaned_query,
            mode=SearchMode.EXACT,
            primary_queries=plan.primary_queries,
            fallback_queries=plan.fallback_queries,
            format_hint=plan.format_hint,
        )
        return await self._within_deadline(self._search_books_only(plan), plan)

    async def search_author(self, query: str, *, plan: SearchPlan | None = None) -> SearchOutcome:
        plan = plan or SearchPlan(query, query, SearchMode.AUTHOR, (query,), ())
        return await self._within_deadline(self._search_author_only(plan), plan)

    async def search_author_title(
        self,
        author: str,
        title: str,
        *,
        plan: SearchPlan | None = None,
    ) -> SearchOutcome:
        plan = plan or SearchPlan(
            original_query=f"{title} {author}",
            cleaned_query=f"{title} {author}",
            mode=SearchMode.AUTHOR_TITLE,
            primary_queries=(title, f"{title} {author}"),
            fallback_queries=(),
            author=author,
            title=title,
        )
        return await self._within_deadline(self._search_author_title(plan), plan)

    async def _search_general(self, plan: SearchPlan) -> SearchOutcome:
        # Book search is the common path. Do not make a fast title lookup wait
        # for the independent (and often slower) author endpoint.
        try:
            books = await self._search_books_only(plan)
        except Exception as book_error:
            raise _as_flibusta_error(book_error) from book_error
        if books.books:
            return books

        try:
            authors = await self._search_author_only(plan)
        except Exception as author_error:
            raise _as_flibusta_error(author_error) from author_error
        return SearchOutcome(
            plan,
            [],
            authors.authors,
            tuple(dict.fromkeys((*books.used_queries, *authors.used_queries))),
        )

    async def _search_exact(self, plan: SearchPlan) -> SearchOutcome:
        books = await self._search_books_only(plan)
        if books.books:
            return books
        authors = await self._search_author_only(plan)
        return SearchOutcome(
            plan,
            [],
            authors.authors,
            tuple(dict.fromkeys((*books.used_queries, *authors.used_queries))),
        )

    async def _search_books_only(self, plan: SearchPlan) -> SearchOutcome:
        used: list[str] = []
        errors: list[Exception] = []
        candidates: list[SearchResult] = []
        queries = tuple(dict.fromkeys((*plan.primary_queries, *plan.fallback_queries)))
        for index, query in enumerate(queries):
            used.append(query)
            try:
                books = rank_and_dedupe_books(
                    await self._request("search", query, limit=self.book_limit),
                    plan.cleaned_query,
                )
            except Exception as exc:
                errors.append(exc)
                continue
            books = [book for book in books if _candidate_matches(plan, book)]
            if books:
                candidates.extend(books)
                ranked = rank_and_dedupe_books(candidates, plan.cleaned_query)
                state = _search_run.get()
                self._remember(plan, ranked)
                if state is None or not state.expanded:
                    return SearchOutcome(plan, ranked, [], tuple(used), has_more=index + 1 < len(queries))
        best = rank_and_dedupe_books(candidates, plan.cleaned_query)
        if errors and not best:
            raise _as_flibusta_error(errors[0])
        return SearchOutcome(plan, best, [], tuple(used))

    async def _search_author_only(self, plan: SearchPlan) -> SearchOutcome:
        query = canonical_author_query(plan.author or plan.cleaned_query)
        candidates = await self._request("search_authors", query, limit=self.author_limit)
        authors = rank_authors([item for item in candidates if author_name_matches(query, item.name)], query)
        self._remember(plan, [], authors)
        suggestion = None if authors else suggest_author_query(query, [item.name for item in candidates])
        return SearchOutcome(plan, [], authors, (query,), suggested_query=suggestion)

    async def _search_author_intent(self, plan: SearchPlan) -> SearchOutcome:
        # Author intent is sticky: an empty/error author response must never
        # become a literal book search returning biographies of that author.
        try:
            outcome = await self._search_author_only(plan)
        except Exception:
            return SearchOutcome(plan, [], [], (), suggested_query=suggest_author_query(plan.author or plan.cleaned_query))
        if len(outcome.authors) != 1:
            return outcome
        author = outcome.authors[0]
        try:
            page_name, books = await self._request("author_books", author.author_id, limit=self.book_limit)
        except Exception:
            return outcome
        if page_name and not author_name_matches(author.name, page_name):
            status = search_status.get()
            if status is not None:
                status.source_unavailable = True
            return outcome
        works = [SearchResult(book.book_id, book.title, book.author or page_name or author.name)
                 for book in books
                 if not book.author or author_name_matches(author.name, book.author)]
        self._remember(plan, works, outcome.authors)
        return replace(outcome, books=works)

    async def search_about_author(self, query: str) -> SearchOutcome:
        author = canonical_author_query(query)
        plan = SearchPlan(query, author, SearchMode.EXACT, (author,), ())
        async def lookup():
            books = await self._request("search", author, limit=self.book_limit)
            about = [book for book in books if author_name_matches(author, book.title)]
            self._remember(plan, about)
            return SearchOutcome(plan, about, [], (author,), about_author=True)
        return await self._within_deadline(lookup(), plan)

    async def _search_author_title_with_fallback(self, plan: SearchPlan) -> SearchOutcome:
        try:
            outcome = await self._search_author_title(plan)
        except Exception:
            outcome = SearchOutcome(plan, [], [], ())
        if outcome.books:
            return outcome
        try:
            general = await self._search_books_only(plan)
        except Exception:
            if outcome.authors:
                return outcome
            raise
        return SearchOutcome(
            plan,
            general.books,
            outcome.authors or general.authors,
            tuple(dict.fromkeys((*outcome.used_queries, *general.used_queries))),
        )

    async def _search_author_title(self, plan: SearchPlan) -> SearchOutcome:
        author = plan.author or ""
        title = plan.title or plan.cleaned_query
        errors: list[Exception] = []
        try:
            books = await self._request("search", title, limit=self.book_limit)
        except Exception as exc:
            books = []
            errors.append(exc)
        matched = [book for book in books if _candidate_matches(plan, book)]
        self._remember(plan, matched)
        if matched:
            return SearchOutcome(
                plan,
                rank_and_dedupe_books(matched, title),
                [],
                (title,),
            )

        try:
            authors = await self._request("search_authors", author, limit=min(self.author_limit, 10))
        except Exception as exc:
            authors = []
            errors.append(exc)
        ranked_authors = rank_authors([item for item in authors if author_name_matches(author, item.name)], author)
        self._remember(plan, [], ranked_authors)
        matched = await self._expand_author_books(author, title, ranked_authors)
        self._remember(plan, matched, ranked_authors)
        if errors and not matched and not authors:
            raise _as_flibusta_error(errors[0])
        return SearchOutcome(
            plan,
            rank_and_dedupe_books(matched, title),
            ranked_authors,
            (title, author),
        )

    async def _expand_author_books(
        self,
        author: str,
        title: str,
        authors: list[AuthorResult],
    ) -> list[SearchResult]:
        for candidate in authors[:2]:
            if not author_name_matches(author, candidate.name):
                continue
            try:
                author_name, books = await self._request(
                    "author_books", candidate.author_id,
                    limit=self.book_limit,
                )
            except Exception:
                continue
            matched = [
                SearchResult(book.book_id, book.title, book.author or author_name or candidate.name)
                for book in books
                if _candidate_matches(
                    SearchPlan(title, title, SearchMode.AUTHOR_TITLE, (), (), author=author, title=title),
                    SearchResult(book.book_id, book.title, book.author or author_name or candidate.name),
                )
            ]
            if matched:
                return matched
        return []

    async def _request(self, method, query, **kwargs):
        state = _search_run.get()
        key = (method, query.casefold(), tuple(kwargs.items()))
        if state is not None:
            if key in state.responses:
                value = state.responses[key]
                if isinstance(value, Exception):
                    raise value
                return value
            state.used.append(query)
        try:
            value = await getattr(self.client, method)(query, **kwargs)
        except Exception as exc:
            status = search_status.get()
            if status is not None:
                status.source_unavailable = True
            if state is not None:
                state.responses[key] = exc
            raise
        if state is not None:
            state.responses[key] = value
        return value

    def _remember(self, plan, books, authors=()):
        state = _search_run.get()
        if state is not None:
            state.books = rank_and_dedupe_books([*state.books, *books], plan.title or plan.cleaned_query)[:self.book_limit]
            state.authors = list({item.author_id: item for item in [*state.authors, *authors]}.values())[:self.author_limit]

    async def _within_deadline(self, operation, plan, *, expanded=False):
        state = _SearchRun(expanded=expanded)
        status = SearchStatus()
        run_token = _search_run.set(state)
        status_token = search_status.set(status)
        try:
            try:
                async with asyncio.timeout(max(0.05, self.timeout_seconds)):
                    outcome = await operation
            except TimeoutError:
                if not state.books and not state.authors:
                    raise
                status.source_unavailable = True
                outcome = SearchOutcome(plan, state.books, state.authors, tuple(state.used), partial=True, has_more=True)
            books = rank_and_dedupe_books(outcome.books, plan.title or plan.cleaned_query)[:self.book_limit]
            approximate = bool(books and plan.mode != SearchMode.AUTHOR and not outcome.about_author
                               and not title_matches(plan.title or plan.cleaned_query, books[0].title))
            return replace(
                outcome, plan=plan, books=books, approximate=approximate,
                source_unavailable=status.source_unavailable, stale_at=status.stale_at,
                has_more=outcome.has_more and not expanded,
            )
        finally:
            search_status.reset(status_token)
            _search_run.reset(run_token)


def author_name_matches(expected: str, actual: str) -> bool:
    return names_match(expected, actual)


def title_matches(expected: str, actual: str) -> bool:
    expected_norm = norm(base_title(expected))
    actual_norm = norm(base_title(actual))
    if not expected_norm or not actual_norm:
        return False
    return actual_norm == expected_norm or f" {expected_norm} " in f" {actual_norm} "


def _as_flibusta_error(exc: Exception) -> FlibustaError:
    if isinstance(exc, FlibustaError):
        return exc
    if isinstance(exc, TimeoutError):
        return FlibustaError("Flibusta не ответила вовремя. Попробуй ещё раз через минуту.")
    return FlibustaError("Не удалось подключиться к Flibusta.")


def _candidate_matches(plan: SearchPlan, book: SearchResult) -> bool:
    expected = plan.title or plan.cleaned_query
    if plan.mode == SearchMode.AUTHOR_TITLE:
        # A person-like title (e.g. Евгений Онегин) can fool the intent heuristic.
        # An exact whole-query title is evidence to recover without broadening
        # ordinary author+title requests to unrelated books.
        if (len(plan.cleaned_query.split()) == 2
                and plan.primary_queries == (plan.cleaned_query,)
                and norm(plan.cleaned_query) == norm(base_title(book.title))):
            return True
        if not book.author or not author_name_matches(plan.author or "", book.author):
            return False
        return title_matches(expected, book.title) or SequenceMatcher(None, norm(expected), norm(base_title(book.title))).ratio() >= 0.85
    if is_strong_book_match(book, expected):
        return True
    tokens = set(norm(expected).split())
    title_tokens = set(norm(base_title(book.title)).split())
    return bool(tokens and len(tokens & title_tokens) / len(tokens) >= 0.5)
