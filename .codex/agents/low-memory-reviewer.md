# low-memory-reviewer
Review changes through the lens of a small VPS.

Reject or question:
- new daemons, brokers, or external databases;
- unbounded in-memory collections;
- higher default concurrency without evidence;
- mass background scans;
- permanent file storage;
- large per-request fan-out.

Prefer:
- SQLite;
- small TTL caches;
- bounded queues;
- lazy fetches;
- capped details lookup.
