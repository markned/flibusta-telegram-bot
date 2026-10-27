# Author intent and search clarification

The reported query `лев толстой` was already recognized as an author query. The
incorrect output came from a second stage: an empty/error author endpoint triggered
an unconstrained book search, accepting biographies with the name in their title.
Separately, author_name_matches accepted any author with the same surname, so
`Лев Толстой` also matched `Алексей Толстой`.

## Current behavior

- Author intent remains author intent after empty results and source failures.
  There is no automatic title-search fallback or biography substitution.
- Matching requires the specified given name and surname. Catalog name ordering
  may differ. An omitted patronymic is allowed; conflicting explicit patronymics
  are rejected, and a patronymic in the user's query is not discarded.
- A unique author result loads at most one author page and exposes up to the
  existing book_limit works. Each byline must match that author; missing bylines
  inherit the identity of the validated author page. A wrong page identity or a
  source error preserves the author link with failure provenance, not wrong books.
- Multiple matching authors require a choice; their catalogs are not expanded
  speculatively. `Толстой` does not silently choose Lev over Alexey.
- `книги Льва Толстого` and `произведения Фёдора Достоевского` resolve to author
  queries. Genitive aliases apply to known name tokens only; arbitrary title
  suffixes are never rewritten.
- The existing name hints now consistently include full Agatha Christie, Jane
  Austen, Stephen King and Alexey Tolstoy identities. Alias metadata is shared by
  routing and matching rather than duplicated. IDs always come from Flibusta.
- A single edit or adjacent transposition with one unambiguous candidate produces
  an explicit confirmation suggestion. Candidates can come from the bounded
  author response or known names. No book is loaded under a silently changed name.
- `Книги об авторе` is a separate action in web and Telegram, filtering matches by
  the name in the book title instead of its author field. It does not run during
  ordinary author search. Quoted names still force a literal title search.
- Author works are labeled as works, not approximate title matches against the
  author's name. Empty author results say that an author was not found.

The web implementation uses server-rendered links; no JS/framework dependencies.
The new Telegram confirmation/about callbacks verify user AND chat ownership and
copy the human identity onto the callback message before invoking a search. Result
sessions remain in the existing bounded store; no new unbounded cache or worker.
The explicit `/author` command continues returning author choices without fetching
unused work lists. Search deadlines/cache/resource bounds are preserved.

## Verification and limits

`tests/test_author_intent.py` reproduces the original empty/error fallback, namesake
matching, surname ambiguity, genitive forms, typo clarification, page failure and
patronymic cases. UI tests exercise web about links and confirmation links,
Telegram author works and ownership. Existing title, golden, cache, download and
reader tests stay in place. `make test-search` includes the new regression suite.

Run with a dummy Telegram token:

```
TELEGRAM_BOT_TOKEN=123456:dummy make check test-search test-kindle search-quality
make search-benchmark
```

These are deterministic rules, not universal language understanding. Unknown names
can be supplied explicitly as `книги <имя автора>` or via `/author`; the alias set
is not an exhaustive author dictionary or general morphology library. Multiple
possible spell corrections are not guessed. Author-page lookup is capped and
subject to the existing deadline, so not all works are promised in one response.
No real Flibusta request, book download, SMTP delivery or production access is
required by these tests. Live recall and source availability are not established
by an offline passing test, and no production speedup is claimed.

## Общий механизм исправлений

Прежнее ограничение одной правкой в каждом из двух слов заменено общей оценкой
запроса по кандидатам каталога: пропуски, вставки, замены, перестановки, инициалы
и порядок слов. Учитывается весь запрос, а при близких вариантах требуется
уточнение. Известные имена — только начальный набор; новые имена берутся из
Flibusta и ограниченного публичного словаря SQLite. Коррекция подтверждается
до открытия автора. Подробности и проверка: [fuzzy-search.md](fuzzy-search.md).
