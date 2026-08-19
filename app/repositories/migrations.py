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
(4,'004_add_library_tables','''
CREATE TABLE IF NOT EXISTS flibusta_cache (cache_key TEXT PRIMARY KEY, cache_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_flibusta_cache_type ON flibusta_cache(cache_type);
CREATE INDEX IF NOT EXISTS idx_flibusta_cache_expires ON flibusta_cache(expires_at);
CREATE TABLE IF NOT EXISTS user_favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, book_id TEXT NOT NULL, title TEXT NOT NULL, author TEXT, created_at TEXT NOT NULL, UNIQUE(user_id, book_id));
CREATE INDEX IF NOT EXISTS idx_user_favorites_user_created ON user_favorites(user_id, created_at);
CREATE TABLE IF NOT EXISTS download_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, book_id TEXT NOT NULL, title TEXT, author TEXT, format TEXT NOT NULL, filename TEXT, file_size_bytes INTEGER, delivery_target TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT);
CREATE INDEX IF NOT EXISTS idx_download_history_user_created ON download_history(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_download_history_target_status_created ON download_history(delivery_target, status, created_at);
CREATE TABLE IF NOT EXISTS user_last_books (user_id INTEGER PRIMARY KEY, book_id TEXT NOT NULL, title TEXT NOT NULL, author TEXT, source TEXT NOT NULL, updated_at TEXT NOT NULL);
'''),
(5,'005_add_access_tables','''
CREATE TABLE IF NOT EXISTS access_users (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, status TEXT NOT NULL, requested_at TEXT NOT NULL, approved_at TEXT, approved_by INTEGER);
CREATE INDEX IF NOT EXISTS idx_access_users_status ON access_users(status);
CREATE TABLE IF NOT EXISTS invite_codes (code TEXT PRIMARY KEY, created_by INTEGER NOT NULL, max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS invite_uses (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, user_id INTEGER NOT NULL, used_at TEXT NOT NULL);
'''),

(6,'006_add_kindle_sender_confirmation','''
ALTER TABLE user_kindle_settings ADD COLUMN approved_sender_confirmed INTEGER DEFAULT 0;
'''),
(7,'007_add_reader_destinations','''
CREATE TABLE IF NOT EXISTS user_reader_settings (
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    destination_email TEXT NOT NULL,
    preferred_format TEXT NOT NULL DEFAULT 'epub',
    enabled INTEGER NOT NULL DEFAULT 1,
    sender_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_user_reader_settings_provider_user ON user_reader_settings(provider, user_id);
ALTER TABLE kindle_deliveries ADD COLUMN provider TEXT NOT NULL DEFAULT 'kindle';
'''),
(8,'008_add_web_access','''
CREATE TABLE IF NOT EXISTS web_pairing_codes (
    code_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_web_pairing_codes_user_expires
    ON web_pairing_codes(user_id, expires_at);
CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_user_created
    ON web_sessions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expires
    ON web_sessions(expires_at);
'''),
(9,'009_add_user_announcements','''
CREATE TABLE IF NOT EXISTS user_announcements (
    user_id INTEGER NOT NULL,
    announcement_id TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (user_id, announcement_id)
);
CREATE INDEX IF NOT EXISTS idx_user_announcements_id
    ON user_announcements(announcement_id, sent_at);
'''),
(10,'010_add_search_miss_stats','''
CREATE TABLE IF NOT EXISTS search_miss_stats (
    day TEXT NOT NULL,
    mode TEXT NOT NULL,
    length_bucket TEXT NOT NULL,
    keyboard_variant INTEGER NOT NULL,
    fallback_count INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (day, mode, length_bucket, keyboard_variant, fallback_count)
);
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
