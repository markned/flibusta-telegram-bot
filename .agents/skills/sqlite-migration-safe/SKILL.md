---
name: sqlite-migration-safe
description: "Add or review idempotent SQLite migrations with data preservation, WAL and compatibility checks."
---

Add a numbered migration in app/repositories/migrations.py, never ad-hoc startup schema edits. Inspect Database initialization/transaction boundaries and repositories actually using changed tables. SQLite remains the sole database.
Test an empty tmp_path database, repeated initialization, and an old-schema database seeded with representative user/settings/favorites/history/delivery data. Assert preservation and required indexes using PRAGMA; check rollback on error, concurrent access/busy handling, WAL settings/checkpoints and backward-compatible rolling deploy behavior. Indexes must serve actual queries.
Reuse migration/database tests in tests/test_library_features.py, tests/test_readers.py and tests/test_web.py. Run focused tests then make check. Never use a production database; backup logic tests use temporary files.
