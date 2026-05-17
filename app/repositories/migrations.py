from __future__ import annotations
from datetime import UTC, datetime
import logging
logger=logging.getLogger(__name__)

def now(): return datetime.now(UTC).isoformat()
MIGRATIONS=[
(1,'001_initial_kindle_tables','''
CREATE TABLE IF NOT EXISTS user_kindle_settings (user_id INTEGER PRIMARY KEY, kindle_email TEXT NOT NULL, preferred_kindle_format TEXT DEFAULT 'epub', send_to_kindle_enabled INTEGER DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kindle_deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, book_id TEXT NOT NULL, title TEXT, format TEXT, filename TEXT, file_size_bytes INTEGER, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
'''),
(2,'002_add_delivery_updated_at_index','''
CREATE INDEX IF NOT EXISTS idx_kindle_deliveries_user_created ON kindle_deliveries(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_kindle_deliveries_status_created ON kindle_deliveries(status, created_at);
CREATE INDEX IF NOT EXISTS idx_user_kindle_settings_user ON user_kindle_settings(user_id);
ALTER TABLE kindle_deliveries ADD COLUMN retry_of_delivery_id INTEGER;
ALTER TABLE kindle_deliveries ADD COLUMN attempts INTEGER DEFAULT 0;
ALTER TABLE kindle_deliveries ADD COLUMN last_error TEXT;
ALTER TABLE kindle_deliveries ADD COLUMN last_attempt_at TEXT;
'''),
(3,'003_add_user_preferences_table_if_needed','''
CREATE TABLE IF NOT EXISTS user_preferences (user_id INTEGER PRIMARY KEY, preferred_download_format TEXT, preferred_kindle_format TEXT DEFAULT 'epub', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
'''),
]
async def run_migrations(conn):
 await conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)')
 rows=await (await conn.execute('SELECT version FROM schema_migrations')).fetchall(); applied={r[0] for r in rows}
 for version,name,sql in MIGRATIONS:
  if version in applied: continue
  try:
   for statement in [s.strip() for s in sql.split(';') if s.strip()]:
    try: await conn.execute(statement)
    except Exception as exc:
     # ALTER ADD COLUMN is the only non-idempotent statement here
     if 'duplicate column name' not in str(exc).lower(): raise
   await conn.execute('INSERT INTO schema_migrations VALUES (?,?,?)',(version,name,now()))
   logger.info('applied migration version=%s name=%s',version,name)
  except Exception:
   logger.exception('migration failed version=%s name=%s',version,name); raise
 await conn.commit()
