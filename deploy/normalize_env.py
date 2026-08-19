#!/usr/bin/env python3
"""Safely normalize production .env without reading secrets into logs."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile


REMOVED_PREFIXES = (
    "AI_",
    "OPENAI_",
    "DISCOVERY_",
    "RECOMMENDATION_CONFIRMATION_",
    "LITERARY_",
)
REMOVED_KEYS = {"UI_HOME_INLINE_BUTTONS"}

# Conservative values for the current ~1 GB VPS. Only operational knobs are
# managed here; tokens, SMTP credentials, addresses and admin IDs are preserved.
MANAGED_VALUES = {
    "LOG_LEVEL": "INFO",
    "FLIBUSTA_RETRIES": "3",
    "FLIBUSTA_RETRY_DELAY_SECONDS": "1.5",
    "SEARCH_RESULTS_LIMIT": "40",
    "SEARCH_TOTAL_TIMEOUT_SECONDS": "35",
    "SEARCH_SOURCE_TIMEOUT_SECONDS": "25",
    "SEARCH_FALLBACK_MAX_QUERIES": "3",
    "CACHE_ENABLED": "true",
    "CACHE_BOOK_SEARCH_TTL_SECONDS": "21600",
    "CACHE_AUTHOR_SEARCH_TTL_SECONDS": "21600",
    "CACHE_SMART_SEARCH_TTL_SECONDS": "21600",
    "CACHE_BOOK_DETAILS_TTL_SECONDS": "86400",
    "CACHE_AUTHOR_BOOKS_TTL_SECONDS": "86400",
    "CACHE_STALE_IF_ERROR_SECONDS": "604800",
    "FLIBUSTA_CIRCUIT_BREAKER_FAILURES": "3",
    "FLIBUSTA_CIRCUIT_BREAKER_COOLDOWN_SECONDS": "30",
    "KINDLE_WORKER_CONCURRENCY": "1",
    "KINDLE_USER_CONCURRENCY": "1",
    "UI_HIDE_COMMAND_MENU_FOR_USERS": "true",
    "UI_SHOW_ADMIN_COMMANDS": "false",
    "UI_SHOW_POWER_USER_COMMANDS": "false",
    "UI_REPLY_KEYBOARD_ENABLED": "true",
    "WEB_ENABLED": "true",
    "WEB_HOST": "127.0.0.1",
    "WEB_PORT": "8081",
    "WEB_PUBLIC_URL": "https://books.technique.ink",
    "WEB_PAIR_CODE_TTL_SECONDS": "600",
    "WEB_SESSION_DAYS": "90",
    "WEB_MAX_SESSIONS_PER_USER": "5",
    "WEB_DOWNLOAD_MAX_MB": "45",
    "WEB_DOWNLOAD_CONCURRENCY": "1",
    "WEB_COOKIE_SECURE": "true",
}

_REMOVED_COMMENT_MARKERS = (
    "openai",
    "tavily",
    "discovery",
    "recommendation",
    "literary",
    "ai assistant",
)


def normalize_env(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise FileNotFoundError(f"Environment file not found: {path}")

    original = path.read_text(encoding="utf-8")
    backup = path.with_name(f"{path.name}.backup")
    if not backup.exists():
        shutil.copy2(path, backup)
    os.chmod(backup, 0o600)

    kept: list[str] = []
    removed = 0
    managed_seen: set[str] = set()

    for raw_line in original.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#") and any(
            marker in stripped.casefold() for marker in _REMOVED_COMMENT_MARKERS
        ):
            removed += 1
            continue
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            kept.append(raw_line.rstrip())
            continue

        key = raw_line.split("=", 1)[0].strip()
        if key.startswith(REMOVED_PREFIXES) or key in REMOVED_KEYS:
            removed += 1
            continue
        if key in MANAGED_VALUES:
            if key not in managed_seen:
                kept.append(f"{key}={MANAGED_VALUES[key]}")
                managed_seen.add(key)
            else:
                removed += 1
            continue
        kept.append(raw_line.rstrip())

    missing = [key for key in MANAGED_VALUES if key not in managed_seen]
    if missing:
        if kept and kept[-1]:
            kept.append("")
        kept.append("# Deterministic search / low-memory production defaults")
        kept.extend(f"{key}={MANAGED_VALUES[key]}" for key in missing)

    if not any(line.startswith("WEB_AUTH_SECRET=") and line.split("=", 1)[1].strip() for line in kept):
        kept = [line for line in kept if not line.startswith("WEB_AUTH_SECRET=")]
        kept.append(f"WEB_AUTH_SECRET={secrets.token_urlsafe(32)}")
        missing.append("WEB_AUTH_SECRET")

    compact: list[str] = []
    for line in kept:
        if line or not compact or compact[-1]:
            compact.append(line)
    rendered = "\n".join(compact).rstrip() + "\n"

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)

    return removed, len(missing)


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    try:
        removed, added = normalize_env(path)
    except Exception as exc:
        print(f"Environment normalization failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"Environment normalized safely: removed={removed}, added={added}, backup=yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
