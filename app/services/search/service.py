from __future__ import annotations

import asyncio
from typing import Protocol

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

    async def search(self, query: str) -> SearchOutcome:
        plan = build_search_plan(query, max_fallback_queries=self.max_fallback_queries)
        if plan.mode == SearchMode.UNSUPPORTED_TOPIC:
            return SearchOutcome(plan, [], [], ())
        if plan.mode == SearchMode.AUTHOR_TITLE:
            return await self._within_deadline(self._search_author_title_with_fallback(plan))
        if plan.mode == SearchMode.AUTHOR:
            return await self._within_deadline(self._search_author_with_fallback(plan))
        if plan.mode == SearchMode.EXACT:
            return await self._within_deadline(self._search_exact(plan))
        return await self._within_deadline(self._search_general(plan))

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
        return await self._within_deadline(self._search_books_only(plan))

    async def search_author(self, query: str, *, plan: SearchPlan | None = None) -> SearchOutcome:
        plan = plan or SearchPlan(query, query, SearchMode.AUTHOR, (query,), ())
        return await self._within_deadline(self._search_author_only(plan))

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
        return await self._within_deadline(self._search_author_title(plan))

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
        for query in (*plan.primary_queries, *plan.fallback_queries):
            used.append(query)
            try:
                books = rank_and_dedupe_books(
                    await self.client.search(query, limit=self.book_limit),
                    plan.cleaned_query,
                )
            except Exception as exc:
                errors.append(exc)
                continue
            if books:
                candidates.extend(books)
                ranked = rank_and_dedupe_books(candidates, plan.cleaned_query)
                if ranked and is_strong_book_match(ranked[0], plan.cleaned_query):
                    return SearchOutcome(plan, ranked, [], tuple(used))
        best = rank_and_dedupe_books(candidates, plan.cleaned_query)
        if errors and not best:
            raise _as_flibusta_error(errors[0])
        return SearchOutcome(plan, best, [], tuple(used))

    async def _search_author_only(self, plan: SearchPlan) -> SearchOutcome:
        query = plan.author or plan.cleaned_query
        authors = rank_authors(
            await self.client.search_authors(query, limit=self.author_limit),
            query,
        )
        return SearchOutcome(plan, [], authors, (query,))

    async def _search_author_with_fallback(self, plan: SearchPlan) -> SearchOutcome:
        try:
            outcome = await self._search_author_only(plan)
        except Exception:
            outcome = SearchOutcome(plan, [], [], ())
        if outcome.authors:
            return outcome
        return await self._search_books_only(_general_fallback_plan(plan))

    async def _search_author_title_with_fallback(self, plan: SearchPlan) -> SearchOutcome:
        try:
            outcome = await self._search_author_title(plan)
        except Exception:
            outcome = SearchOutcome(plan, [], [], ())
        if outcome.books:
            return outcome
        try:
            general = await self._search_books_only(_general_fallback_plan(plan))
        except Exception:
            if outcome.authors:
                return outcome
            raise
        return SearchOutcome(
            plan,
            general.books,
            general.authors,
            tuple(dict.fromkeys((*outcome.used_queries, *general.used_queries))),
        )

    async def _search_author_title(self, plan: SearchPlan) -> SearchOutcome:
        author = plan.author or ""
        title = plan.title or plan.cleaned_query
        errors: list[Exception] = []
        try:
            books = await self.client.search(title, limit=self.book_limit)
        except Exception as exc:
            books = []
            errors.append(exc)
        matched = [book for book in books if book.author and author_name_matches(author, book.author)]
        if matched:
            return SearchOutcome(
                plan,
                rank_and_dedupe_books(matched, title),
                [],
                (title,),
            )

        try:
            authors = await self.client.search_authors(author, limit=min(self.author_limit, 10))
        except Exception as exc:
            authors = []
            errors.append(exc)
        ranked_authors = rank_authors(authors, author)
        matched = await self._expand_author_books(author, title, ranked_authors)
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
                author_name, books = await self.client.author_books(
                    candidate.author_id,
                    limit=self.book_limit,
                )
            except Exception:
                continue
            matched = [
                SearchResult(book.book_id, book.title, book.author or author_name or candidate.name)
                for book in books
                if title_matches(title, book.title)
            ]
            if matched:
                return matched
        return []

    async def _within_deadline(self, operation):
        async with asyncio.timeout(max(0.05, self.timeout_seconds)):
            return await operation


def author_name_matches(expected: str, actual: str) -> bool:
    expected_norm = norm(expected)
    actual_norm = norm(actual)
    if not expected_norm or not actual_norm:
        return False
    if expected_norm in actual_norm or actual_norm in expected_norm:
        return True
    surname = expected_norm.split()[-1]
    return surname in actual_norm.split()


def title_matches(expected: str, actual: str) -> bool:
    expected_norm = norm(base_title(expected))
    actual_norm = norm(base_title(actual))
    if not expected_norm or not actual_norm:
        return False
    return actual_norm == expected_norm or expected_norm in actual_norm or actual_norm in expected_norm


def _as_flibusta_error(exc: Exception) -> FlibustaError:
    if isinstance(exc, FlibustaError):
        return exc
    if isinstance(exc, TimeoutError):
        return FlibustaError("Flibusta не ответила вовремя. Попробуй ещё раз через минуту.")
    return FlibustaError("Не удалось подключиться к Flibusta.")


def _general_fallback_plan(plan: SearchPlan) -> SearchPlan:
    return SearchPlan(
        original_query=plan.original_query,
        cleaned_query=plan.cleaned_query,
        mode=SearchMode.FALLBACK,
        primary_queries=plan.primary_queries or (plan.cleaned_query,),
        fallback_queries=plan.fallback_queries,
        format_hint=plan.format_hint,
    )
