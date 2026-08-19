from app.config import Settings


def test_removed_ai_and_discovery_env_values_are_ignored(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-secret")
    monkeypatch.setenv("DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("DISCOVERY_WEB_API_KEY", "legacy-secret")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "ai_enabled")
    assert not hasattr(settings, "discovery_enabled")
    dumped = settings.model_dump()
    assert "legacy-secret" not in repr(dumped)


def test_search_reliability_settings_load(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("SEARCH_TOTAL_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("SEARCH_SOURCE_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("SEARCH_FALLBACK_MAX_QUERIES", "1")
    monkeypatch.setenv("CACHE_STALE_IF_ERROR_SECONDS", "3600")
    monkeypatch.setenv("FLIBUSTA_CIRCUIT_BREAKER_FAILURES", "4")
    monkeypatch.setenv("FLIBUSTA_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "20")
    settings = Settings(_env_file=None)
    assert settings.search_total_timeout_seconds == 9
    assert settings.search_source_timeout_seconds == 7
    assert settings.search_fallback_max_queries == 1
    assert settings.cache_stale_if_error_seconds == 3600
    assert settings.flibusta_circuit_breaker_failures == 4
    assert settings.flibusta_circuit_breaker_cooldown_seconds == 20
