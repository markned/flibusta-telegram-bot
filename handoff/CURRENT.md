# Current handoff

## Current architecture

- Natural text uses one deterministic `SearchService`.
- Supported search intents: exact title, author, author+title, and safe fallback.
- Broad genre/recommendation phrases are not searched literally; the user is asked for a title or author.
- AI assistant, OpenAI planning, Tavily discovery, recommendation packs, pending confirmations, and literary provider scaffolding have been removed.
- Flibusta remains the only catalog source.
- Search/download/reader delivery/favorites/history/admin flows remain intact.

## Search reliability

- One total search deadline (`SEARCH_TOTAL_TIMEOUT_SECONDS`).
- At most `SEARCH_FALLBACK_MAX_QUERIES` shortened variants.
- Case-insensitive author/title matching.
- One keyboard-layout correction for Latin input.
- Book and author branches run concurrently and preserve partial success.
- Empty responses are not cached.
- Fresh SQLite cache is preferred; recent stale data can be used only when Flibusta fails.
- A small in-memory circuit breaker prevents repeated failing calls.

## Production configuration additions

```env
CACHE_STALE_IF_ERROR_SECONDS=604800
FLIBUSTA_CIRCUIT_BREAKER_FAILURES=3
FLIBUSTA_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30
```

Legacy AI/discovery variables are ignored for rolling-deploy safety and should be removed from the production `.env`.

## Constraints

- Low-memory VPS; no external queue or database.
- No downloaded books stored permanently.
- No real network calls in tests.
- Keep Kindle/PocketBook worker concurrency at 1 in production.
