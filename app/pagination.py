from __future__ import annotations


SEARCH_PAGE_SIZE = 8


def page_items(results: list, page: int) -> list:
    start = page * SEARCH_PAGE_SIZE
    end = start + SEARCH_PAGE_SIZE
    return results[start:end]


def total_pages(total_results: int) -> int:
    return max(1, (total_results + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
