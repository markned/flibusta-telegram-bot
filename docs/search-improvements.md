# Search reliability: first improvement stage

The primary interface for this stage is the web shell. Telegram shares the same
SearchService and remains covered by regression and download/delivery mocks.

## Behavior

- Author+title queries check both identities before accepting a hit. Whole-query
  exact matches can recover ambiguous two-word person-like titles such as
  «Евгений Онегин»; an explicit author+title call retains its author constraint.
- Found authors survive an empty secondary book lookup. Per-request memoization
  avoids reissuing the same catalog call in the same search.
- Initial search returns the first relevant candidate set. Optional remaining
  variants run only after «Искать дополнительные варианты» on the web. Expansion
  repeats the bounded plan using the existing catalog cache, not a background job.
- If expansion reaches its deadline, eligible books/authors already received are
  returned with an incomplete-search notice. External cancellation still propagates.
- Exact and approximate matches have separate sections in web results. Pagination
  exposes books/authors beyond the old 20/12 display cutoffs. Navigation preserves
  the query and expansion mode; each page is rendered server-side with no JS or
  new browser dependencies. Pagination rereads the same bounded search and can
  reflect source changes when caching is disabled or expires; it is not a snapshot.
- An expired but still eligible cache entry gives the source at most one second
  to refresh (also bounded by the existing source timeout). On failure or timeout,
  results include the stored UTC creation date. Existing max-stale policy still
  applies. Fresh cache behavior is unchanged.
- Metadata is request-local, including coalesced requests; cache dates and upstream
  failures cannot leak between unrelated searches. No schema migration is needed.
- Source errors preserve the web query and offer «Повторить поиск». They do not
  become «Ничего не нашлось» and are not recorded as normal search misses.
- Explicit `author — title` supports unlisted names and long titles. A final word
  after a preposition is not automatically interpreted as an author's surname.

This stage adds no new service, database, queue or background worker. Book download
and reader concurrency, access checks, SMTP, conversion and file lifecycle stay intact.
Four exception-message retry lambdas also bind their text explicitly to resolve
pre-existing Ruff F821 findings without suppressing lint rules.

## Popular-book benchmark

`tests/fixtures/popular_books.json` stores the 100-entry source population, the
seed **20261026** and 30 selected ranks. Selection uses `random.Random(seed).sample`;
changing the sample is an explicit fixture update, not a fresh draw per run.
The population is the [Chitai-Gorod 2025 bestseller list](https://www.chitai-gorod.ru/articles/bestsellery-2025-goda-top-10-knig),
with rank/author metadata from its [published full table](https://primamedia.ru/news/2337577/).
The table's brand-as-author entry for Gravity Falls was corrected against the
[retail book record](https://www.chitai-gorod.ru/product/graviti-folz-dnevnik-3-2562533),
with the original value and correction source preserved in the fixture.

Each selected book generates three cases: title, explicit `author — title`, and
one deterministic transposition in the title (90 total). Long retail subtitles
are preserved; this can differ from catalog metadata and requires review in live
results. The sample represents this sales list, not all languages/genres or all
books available in Flibusta.

```
make search-benchmark
# Safe, synthetic lexical catalog; not a network or catalog-availability test.

.venv/bin/python scripts/search_benchmark.py --live --limit 3 --timeout 5 \
  --output /tmp/search-live.json --capture /tmp/search-responses.json
# Explicit live metadata-only check, no downloads. Stops after 3 consecutive errors.

.venv/bin/python scripts/search_benchmark.py --replay /tmp/search-responses.json \
  --output /tmp/search-replay.json
# Offline replay; missing requests are coverage gaps, not empty catalog responses.
```

The tool reports top-five target matches, unresolved availability, source failures,
and elapsed median/p95 separately. A match requires title AND author from the
reference; missing results never establish absence from the catalog. Real book IDs
are retained from live responses. Synthetic fixture IDs are clearly named and
never used by production or asserted to exist in Flibusta. Capture contains only
public benchmark metadata, not private searches; don't capture arbitrary user queries.

## Verified results and limits

See `search-benchmark-results.json` for measured summaries. Both base `5d4f709`
and changed code returned the target in the first five positions on all 90
**synthetic** cases. The base was run in an isolated temporary export of its app,
using the exact same benchmark and dataset. This does not establish improved live
recall or a production speedup. Local timing has no source/network latency.
The synthetic corpus is deliberately simple; failure-path and ranking regressions
are tested separately with targeted adversarial fixtures.

An explicit live run executed 3/90 cases, all ending with source-unavailable status
under a five-second per-case cap. No missing book was declared absent and no
catalog book ID was invented. A full live comparison remains unverified until
Flibusta is reachable from the development environment. No books were downloaded.

Checks: `make check`, `make test-search`, `make test-kindle`, `make search-quality`,
benchmark tests, web/device tests and existing mocked web→book→download/reader-send
flow. No deployment or merge is part of this change.
