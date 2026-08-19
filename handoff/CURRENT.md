# Current handoff

## Product identity

- Public name: **Полка**.
- Telegram profile artwork lives in `assets/polka-avatar.jpg` (upload) and `assets/polka-avatar.png` (source-quality app asset).
- Telegram and web entry point is now **Мои устройства** instead of **Читалки**; the old reply label remains accepted for backwards compatibility.
- `scripts/update_brand.py` applies the name, descriptions and avatar.
- `scripts/announce_rebrand.py` sends the announcement once per approved user; SQLite prevents duplicates.

## Current architecture

- Natural text uses one deterministic `SearchService`.
- Supported search intents: exact title, author, author+title, and safe fallback.
- Broad genre/recommendation phrases are not searched literally; the user is asked for a title or author.
- AI assistant, OpenAI planning, Tavily discovery, recommendation packs, pending confirmations, and literary provider scaffolding have been removed.
- Flibusta remains the only catalog source.
- A lightweight aiohttp web shell reuses the same search, cache, SQLite, favorites, history and reader delivery services.
- Web users pair through a one-time Telegram code; only HMAC hashes of codes and sessions are stored.
- Kindle and PocketBook are detected from `User-Agent`; their book cards hide downloads and expose only the matching configured delivery action. Reliable covers are served through the local `/cover/{book_id}` proxy.
- Android, iOS and desktop browsers retain direct format downloads and both configured reader actions.
- Web CSS avoids flex/grid and `min()` so old e-ink WebKit engines retain readable spacing and controls.
- The web home shows up to six recent unique books; reader delivery leads to a clear success screen.
- Search/download/reader delivery/favorites/history/admin flows remain intact.

## Search reliability

- One total search deadline (`SEARCH_TOTAL_TIMEOUT_SECONDS`, production default 35s).
- Exact title and author+title matches return from the book endpoint without waiting for author search.
- Individual catalog calls have a shorter `SEARCH_SOURCE_TIMEOUT_SECONDS` (production default 25s), allowing stale-cache fallback before the total deadline.
- At most `SEARCH_FALLBACK_MAX_QUERIES` (production default 3) safe variants.
- Case-insensitive author/title matching.
- One keyboard-layout correction for Latin input.
- Book search runs first; author search is only used when no book was found.
- Empty responses are not cached.
- Duplicate editions are collapsed by normalized title + author and clean editions are ranked first.
- A failed primary query no longer prevents the remaining bounded fallback queries from running.
- Fresh SQLite cache is preferred; recent stale data can be used only when Flibusta fails.
- A small in-memory circuit breaker prevents repeated failing calls.

## Production configuration additions

```env
SEARCH_TOTAL_TIMEOUT_SECONDS=35
SEARCH_SOURCE_TIMEOUT_SECONDS=25
SEARCH_FALLBACK_MAX_QUERIES=3
CACHE_STALE_IF_ERROR_SECONDS=604800
FLIBUSTA_CIRCUIT_BREAKER_FAILURES=3
FLIBUSTA_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30
WEB_ENABLED=true
WEB_HOST=127.0.0.1
WEB_PORT=8081
WEB_PUBLIC_URL=https://books.technique.ink
WEB_AUTH_SECRET=<generated during deploy>
```

The application exposes `/health` on `127.0.0.1:8081`. Public HTTPS requires the one-time root setup in `deploy/setup-web-proxy.sh` after DNS points to the Oracle host.

Legacy AI/discovery variables are ignored for rolling-deploy safety and should be removed from the production `.env`.

## Constraints

- Low-memory VPS; no external queue or database.
- No downloaded books stored permanently.
- No real network calls in tests.
- Keep Kindle/PocketBook worker concurrency at 1 in production.
