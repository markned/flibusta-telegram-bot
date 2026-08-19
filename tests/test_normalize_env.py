from pathlib import Path

from deploy.normalize_env import MANAGED_VALUES, normalize_env


def test_normalizer_removes_legacy_ai_and_preserves_secrets(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_BOT_TOKEN=keep-token\n"
        "SMTP_PASSWORD=keep-password\n"
        "OPENAI_API_KEY=remove-me\n"
        "DISCOVERY_WEB_API_KEY=remove-me-too\n"
        "UI_HOME_INLINE_BUTTONS=true\n"
        "SEARCH_TOTAL_TIMEOUT_SECONDS=99\n"
        "SEARCH_TOTAL_TIMEOUT_SECONDS=88\n",
        encoding="utf-8",
    )

    removed, added = normalize_env(env)
    result = env.read_text(encoding="utf-8")

    assert removed >= 3
    assert added > 0
    assert "TELEGRAM_BOT_TOKEN=keep-token" in result
    assert "SMTP_PASSWORD=keep-password" in result
    assert "remove-me" not in result
    assert "UI_HOME_INLINE_BUTTONS" not in result
    assert result.count("SEARCH_TOTAL_TIMEOUT_SECONDS=") == 1
    assert "SEARCH_TOTAL_TIMEOUT_SECONDS=12" in result
    assert "WEB_ENABLED=true" in result
    assert "WEB_PUBLIC_URL=https://books.technique.ink" in result
    assert "WEB_AUTH_SECRET=" in result
    assert "WEB_AUTH_SECRET=\n" not in result
    assert (tmp_path / ".env.backup").is_file()


def test_normalizer_is_idempotent(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=keep\n", encoding="utf-8")
    normalize_env(env)
    first = env.read_text(encoding="utf-8")
    normalize_env(env)
    second = env.read_text(encoding="utf-8")
    assert first == second
    assert all(second.count(f"{key}=") == 1 for key in MANAGED_VALUES)
