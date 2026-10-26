---
name: low-memory-audit
description: "Audit changed code for RAM, concurrency, queues, caches, files and SQLite WAL pressure on Oracle VPS."
---

Use diff and workload bounds first. Inspect relevant background task creation/cleanup, request concurrency, httpx pool limits, bounded queues and TTL cache eviction, coalesced-task map cleanup, cancellation and temporary files. Watch large book buffering and references retained after errors.
Preserve current bounds in handoff/CURRENT.md unless measurements justify a change: reader workers 1, HTTP connections 8/keepalive 4, coalesced tasks 64, SQLite page cache 2 MiB and journal 8 MiB. Check WAL checkpoints, file sizes and retention. Reject new daemons/brokers, unbounded fan-out, background scans and permanent downloads.
Measure offline using synthetic small payloads and stdlib tracemalloc/resource where supported; compare before/after under the same workload, recording platform and peak limits. Do not claim VPS savings from local measurements or probe production. Reuse tests/test_library_features.py, tests/test_backup_sqlite.py and tests/test_product_improvements.py. Run focused tests then make check.
