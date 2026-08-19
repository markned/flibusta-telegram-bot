#!/usr/bin/env python3
"""Create and verify a compact SQLite backup without copying WAL files."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import os
import sqlite3


def create_backup(database: Path, output_dir: Path, keep: int = 14) -> Path:
    if not database.is_file():
        raise FileNotFoundError(database)
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = output_dir / f"polka-{stamp}.sqlite3"
    temporary = target.with_suffix(".tmp")
    with sqlite3.connect(database) as source, sqlite3.connect(temporary) as destination:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("backup integrity check failed")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    backups = sorted(output_dir.glob("polka-*.sqlite3"), reverse=True)
    for stale in backups[max(1, keep):]:
        stale.unlink()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a verified Polka SQLite backup")
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "bot.db"))
    parser.add_argument("--output-dir", default="backups")
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    backup = create_backup(Path(args.database), Path(args.output_dir), args.keep)
    print(backup)


if __name__ == "__main__":
    main()
