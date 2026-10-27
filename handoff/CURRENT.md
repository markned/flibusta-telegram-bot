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
- Broad semantic recommendation phrases are not searched literally; users can browse source-backed weekly popularity and community recommendations by genre.
- AI assistant, OpenAI planning, Tavily discovery, recommendation packs, pending confirmations, and literary provider scaffolding have been removed.
- Flibusta remains the only source of catalog book IDs, details and files. Yandex Books and Litres public rankings supply title/author metadata only.
- A lightweight aiohttp web shell reuses the same search, cache, SQLite, favorites, history and reader delivery services.
- Web users pair through a one-time Telegram code; only HMAC hashes of codes and sessions are stored.
- Kindle and PocketBook are detected from `User-Agent`; their book cards hide downloads and expose only the matching configured delivery action. Reliable covers are served through the local `/cover/{book_id}` proxy.
- Android, iOS and desktop browsers retain direct format downloads and both configured reader actions.
- Web CSS avoids flex/grid and `min()` so old e-ink WebKit engines retain readable spacing and controls.
- The web home shows up to six recent unique books; reader delivery leads to a clear success screen.
- Search/download/reader delivery/favorites/history/admin flows remain intact.
- Book cards expose reliable Flibusta series links in Telegram and web; series pages reuse bounded catalog parsing and SQLite caching.
- Web access shows the number of active browser sessions and lets a user revoke all of them from Telegram.
- Web favorites support local title/author filtering, deterministic sorting and pagination.

## Search reliability

- One total search deadline (`SEARCH_TOTAL_TIMEOUT_SECONDS`, production default 35s).
- Exact title and author+title matches return from the book endpoint without waiting for author search.
- Individual catalog calls have a shorter `SEARCH_SOURCE_TIMEOUT_SECONDS` (production default 25s), allowing stale-cache fallback before the total deadline.
- At most `SEARCH_FALLBACK_MAX_QUERIES` (production default 3) safe variants.
- Case-insensitive author/title matching.
- One keyboard-layout correction for Latin input.
- Natural queries use bounded parallel catalog-author probes and book metadata; explicit/known authors retain sticky author intent. Ambiguous author/title queries ask for confirmation.
- Empty responses are not cached.
- Duplicate editions are collapsed by normalized title + author and clean editions are ranked first.
- A failed primary query no longer prevents the remaining bounded fallback queries from running.
- Fresh SQLite cache is preferred; recent stale data can be used only when Flibusta fails.
- A small in-memory circuit breaker prevents repeated failing calls.
- Polite search prefixes are stripped deterministically; no-result telemetry is stored only as daily aggregates without query text.

## Catalog browsing

- Telegram persistent menu: Моя полка / Каталог; source tops, grouped genres and random book inside. Commands `/popular`, `/genres`, `/random`; web `/browse` mirrors these.
- Weekly source ranking by downloads, not an all-time or invented popularity score.
- Genres use source community recommendations; no AI scheduler or full catalog mirror.
- Ready source-ranked SQLite snapshots; one cancellable sequential worker refreshes daily at 06:00 Europe/Belgrade (initial fill when snapshots are missing/stale). No external lookup when opening a saved top.
- Genre groups/subgenres and source books paginate by 8; collection cards retain owner/chat-bound return to the same page. Random selection uses existing snapshots, never scans catalog.
- Existing access/rate limits, bounded lists and HTTP pools remain; no new production dependencies.
- Details and limitations: `docs/catalog-search.md`.

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
- `scripts/backup_sqlite.py` creates private, integrity-checked SQLite snapshots with bounded retention.
- `/admin_health` reports local SQLite/WAL/disk/cache/queue/session/search-miss health without a network probe or sensitive values.

## Performance and cloud development

- SQLite uses WAL with `synchronous=NORMAL`, a 2 MiB per-connection page cache,
  bounded WAL checkpoints and an 8 MiB journal limit.
- Concurrent identical Flibusta reads are coalesced into one upstream request;
  the in-process task map is capped at 64 and entries are removed on completion.
- Flibusta HTTP connections are capped at 8 with 4 keep-alive sockets.
- Codex Cloud bootstrap scripts live in `.codex/`; see `docs/codex-cloud.md`.
- Cloud development does not replace production: tested PRs merge to `main`,
  then the existing GitHub Actions workflow deploys Oracle.

## Codex development workflows

Project skills now live in `.agents/skills/`, optional agents in `.codex/agents/*.toml`.
See `docs/codex-skills.md` for pinned upstreams, routing, validation and Cloud setup.
These are development-only; production code and requirements are unchanged.

## Search improvement stage

- Search enforces title + author matching, preserves discovered authors, and keeps
  eligible partial results when optional expansion hits the deadline.
- Web results separate approximate matches, paginate beyond the initial display,
  and run further variants only via an explicit expansion link. No JS dependency.
- Expired eligible cache gets a one-second refresh budget, then dated stale results
  on source failure. Search provenance is request-local, including coalesced reads.
- Source failures retain the query/retry action and are not counted as normal misses.
- Seeded 30-book/90-query benchmark and measurements: `docs/search-improvements.md`.
  Offline synthetic coverage passes; live catalog verification is blocked by source
  connectivity, not treated as catalog absence. No real download in validation.

## Author search intent

- Author queries no longer fall back to books mentioning the author's name.
- Full given-name/surname identity is required; surname-only ambiguity asks for
  a choice. Unique matches expose bounded works from a validated author page.
- Known genitive name forms and explicit `книги <автор>` requests are normalized;
  one-edit corrections require confirmation. Explicit about-author action stays
  separate. Web and Telegram labels/links preserve this distinction.
- Details and limitations: `docs/search-author-intent.md`.

## Work editions and author continuation

- Author catalogs use the complete source metadata index via `author_works_page`,
  with actual totals and continuation beyond the old 40-entry cutoff. Telegram
  loads additional batches on demand; web renders pages of 8 works.
- Search, authors, genres and popular lists group known editions by conservative
  work + author identity. All actual edition IDs survive; collections/notes and
  other authors remain separate. Author works sort alphabetically, tops retain rank.
- Cards offer «Издания и тома», numeric volume order, conservative default selection
  and return to the original page. Additional versions load only on demand from
  the validated source author index. Downloads/delivery use the selected actual ID.
- Author HTML is capped at 2 MiB, complete-index loads are serialized/coalesced;
  web work references are owner-bound, expire and have aggregate memory bounds.
- No schema, dependencies, production worker count or reader limits changed.
  Details and validation: `docs/work-editions.md`.

- Shared fuzzy query matching uses length-normalized edit evidence over complete
  catalog names, initials and word order; ambiguity requires confirmation. A bounded
  SQLite lexicon learns public names from catalog responses, not user messages.
- Book acceptance requires support for every meaningful query word, including
  edition/volume labels. HTML search highlighting no longer splits words; search
  cache keys use text-v2. Query/request/time/memory bounds remain in place.
- General validation and limitations: `docs/fuzzy-search.md`.
