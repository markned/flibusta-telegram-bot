"""Reproducible popular-book search benchmark; no book downloads.

Default mode uses an explicitly synthetic lexical catalog, not Flibusta evidence.
Use --live for a bounded opt-in catalog check or --replay for captured responses.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from difflib import SequenceMatcher
import importlib.util
import json
from pathlib import Path
import random
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.flibusta import AuthorResult, FlibustaClient, SearchResult
from app.services.search import SearchService
from app.services.search_logic import norm

DATASET = ROOT / "tests/fixtures/popular_books.json"


def load_books(path=DATASET):
    data = json.loads(Path(path).read_text())
    sample = sorted(random.Random(data["selection"]["seed"]).sample(data["population"], 30), key=lambda b: b["rank"])
    if [b["rank"] for b in sample] != data["selected_ranks"]:
        raise ValueError("Selection differs from pinned population/seed")
    return data, sample


def typo(title):
    # One transposition, deterministic; don't change the reference title.
    for word in title.split():
        if len(word) >= 5:
            return title.replace(word, word[:1] + word[2] + word[1] + word[3:], 1)
    return title + "а"


def build_cases(books):
    return [{"rank": b["rank"], "title": b["title"], "author": b["author"], "kind": kind, "query": query}
            for b in books for kind, query in (
                ("title", b["title"]), ("author_title", f'{b["author"]} — {b["title"]}'),
                ("typo", typo(b["title"])),
            )]


class SyntheticCatalog:
    """Controlled lexical corpus for algorithm tests, never catalog book IDs."""
    def __init__(self, population):
        self.books = [SearchResult(f'fixture-{b["rank"]}', b["title"], b["author"]) for b in population]

    async def search(self, query, limit=40):
        terms = set(norm(query).split())
        def score(book):
            words = set(norm(f"{book.title} {book.author}").split())
            exact = len(terms & words)
            fuzzy = max((SequenceMatcher(None, term, word).ratio() for term in terms for word in words), default=0)
            return exact, fuzzy
        # Mimic noisy catalog ordering, leaving ranking to SearchService.
        candidates = [b for b in self.books if score(b)[0] >= max(1, len(terms) // 2) or
                      (len(terms) == 1 and score(b)[1] >= 0.65)]
        return sorted(candidates, key=lambda b: score(b), reverse=True)[:limit][::-1]

    async def search_authors(self, query, limit=20):
        return [AuthorResult(b.book_id, b.author) for b in self.books if norm(query) in norm(b.author or "")][:limit]

    async def author_books(self, author_id, limit=40):
        book = next(b for b in self.books if b.book_id == author_id)
        return book.author, [b for b in self.books if b.author == book.author][:limit]


class RecordingCatalog:
    def __init__(self, client=None, responses=None):
        self.client = client
        self.responses = responses if responses is not None else {}
        self.missing_requests = 0

    async def request(self, method, query, limit):
        key = json.dumps([method, query, limit], ensure_ascii=False)
        if self.client is None:
            entry = self.responses.get(key)
            if entry is None:
                self.missing_requests += 1
                raise RuntimeError("Replay does not cover this request")
            if "error" in entry:
                raise RuntimeError(entry["error"])
        else:
            try:
                result = await getattr(self.client, method)(query, limit=limit)
                if method == "author_books":
                    entry = {"name": result[0], "books": [asdict(b) for b in result[1]]}
                else:
                    entry = {"rows": [asdict(b) for b in result]}
            except Exception as exc:
                self.responses[key] = {"error": type(exc).__name__}
                raise
            self.responses[key] = entry
        if method == "author_books":
            return entry["name"], [SearchResult(**b) for b in entry["books"]]
        row_type = AuthorResult if method == "search_authors" else SearchResult
        return [row_type(**b) for b in entry["rows"]]

    async def search(self, query, limit=40):
        return await self.request("search", query, limit)

    async def search_authors(self, query, limit=20):
        return await self.request("search_authors", query, limit)

    async def author_books(self, query, limit=40):
        return await self.request("author_books", query, limit)


def service_class(path):
    if not path:
        return SearchService
    spec = importlib.util.spec_from_file_location("benchmark_baseline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.SearchService


async def benchmark(args):
    dataset, books = load_books()
    cases = build_cases(books)
    raw = None
    if args.live:
        raw = FlibustaClient(args.base_url, timeout=args.timeout, retries=1, retry_delay=0)
        client = RecordingCatalog(raw)
        mode = "live"
    elif args.replay:
        snapshot = json.loads(Path(args.replay).read_text())
        client = RecordingCatalog(responses=snapshot["responses"])
        mode = "replay (local timing, not source latency)"
    else:
        client = SyntheticCatalog(dataset["population"])
        mode = "synthetic (fixture IDs; no evidence of Flibusta availability)"
    service = service_class(args.service_file)(client, book_limit=40, author_limit=20,
                                                timeout_seconds=args.timeout, max_fallback_queries=3)
    results = []
    failures = 0
    try:
        for case in cases[:args.limit]:
            start = perf_counter()
            missing_before = getattr(client, "missing_requests", 0)
            try:
                outcome = await service.search(case["query"])
                matches = [(n, b) for n, b in enumerate(outcome.books, 1)
                           if norm(b.title) == norm(case["title"]) and norm(b.author or "") == norm(case["author"])]
                position, book = matches[0] if matches else (None, None)
                status = "matched" if matches else ("source_unavailable" if getattr(outcome, "source_unavailable", False) else "unresolved_availability")
                failures = failures + 1 if status == "source_unavailable" else 0
            except Exception:
                position, book = None, None
                status = "source_unavailable"
                failures += 1
            if getattr(client, "missing_requests", 0) > missing_before:
                status = "replay_incomplete"
                position, book = None, None
            results.append({**case, "status": status, "position": position,
                            "book_id": book.book_id if book else None,
                            "seconds": round(perf_counter() - start, 4)})
            if args.live:
                if failures >= 3:
                    break  # Circuit for benchmark only, no background retries.
                await asyncio.sleep(0.5)
    finally:
        if raw is not None:
            await raw.close()
    times = [r["seconds"] for r in results]
    report = {"mode": mode, "seed": dataset["selection"]["seed"], "selected_books": len(books),
              "planned_cases": len(cases), "executed_cases": len(results),
              "top5_matches": sum(r["position"] is not None and r["position"] <= 5 for r in results),
              "unresolved_availability": sum(r["status"] == "unresolved_availability" for r in results),
              "source_unavailable": sum(r["status"] == "source_unavailable" for r in results),
              "replay_incomplete": sum(r["status"] == "replay_incomplete" for r in results),
              "median_seconds": round(statistics.median(times), 4) if times else None,
              "p95_seconds": sorted(times)[min(len(times) - 1, int(len(times) * .95))] if times else None,
              "warning": "No missing result establishes catalog absence. Live/replay metadata must be reviewed before declaring availability or absence.",
              "results": results}
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.capture and isinstance(client, RecordingCatalog):
        Path(args.capture).write_text(json.dumps({"mode": mode, "responses": client.responses}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true")
    modes.add_argument("--replay", type=Path)
    parser.add_argument("--base-url", default="https://flibusta.is")
    parser.add_argument("--limit", type=int, choices=range(1, 91), default=90)
    parser.add_argument("--timeout", type=float, default=8)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--service-file", type=Path, help="Trusted local baseline module for offline comparison only")
    args = parser.parse_args()
    if args.timeout <= 0 or (args.live and args.service_file):
        parser.error("positive timeout required; alternate service is offline-only")
    asyncio.run(benchmark(args))


if __name__ == "__main__":
    main()
