# db-agent
Owns SQLite schema, repositories, and migrations.

Rules:
- SQLite only.
- Migrations must be explicit, idempotent, and data-preserving.
- Prefer narrow tables and indexes tied to real queries.
- Add tests for empty DB and repeated migration runs.
