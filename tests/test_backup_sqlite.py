import sqlite3

from scripts.backup_sqlite import create_backup


def test_backup_is_verified_private_and_rotated(tmp_path) -> None:
    database = tmp_path / "bot.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO books(title) VALUES ('Дюна')")
    output = tmp_path / "backups"
    backup = create_backup(database, output, keep=1)
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT title FROM books").fetchone()[0] == "Дюна"
