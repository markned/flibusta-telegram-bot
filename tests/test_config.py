from app.config import Settings


def test_settings_load_discovery_env_without_activating_web(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'token')
    monkeypatch.setenv('DISCOVERY_ENABLED', 'true')
    monkeypatch.setenv('DISCOVERY_USE_WEB', 'false')
    monkeypatch.setenv('DISCOVERY_WEB_PROVIDER', 'tavily')
    monkeypatch.setenv('DISCOVERY_WEB_API_KEY', 'secret-from-env')
    monkeypatch.setenv('DISCOVERY_MAX_WEB_RESULTS', '5')
    monkeypatch.setenv('DISCOVERY_MAX_WEB_SNIPPET_CHARS', '500')
    monkeypatch.setenv('DISCOVERY_MAX_BOOK_IDEAS', '12')
    monkeypatch.setenv('DISCOVERY_MAX_FLIBUSTA_CHECKS', '8')
    monkeypatch.setenv('DISCOVERY_MAX_FINAL_RESULTS', '10')
    monkeypatch.setenv('DISCOVERY_CACHE_TTL_SECONDS', '604800')
    monkeypatch.setenv('DISCOVERY_USER_DAILY_LIMIT', '5')
    monkeypatch.setenv('DISCOVERY_GLOBAL_DAILY_LIMIT', '50')
    monkeypatch.setenv('DISCOVERY_CONCURRENCY', '1')
    monkeypatch.setenv('DISCOVERY_TIMEOUT_SECONDS', '15')
    settings = Settings(_env_file=None)
    assert settings.discovery_enabled is True
    assert settings.discovery_web_configured is True
    assert settings.discovery_web_active is False
    assert settings.discovery_max_web_results == 5
    assert settings.discovery_timeout_seconds == 15


def test_settings_safe_discovery_defaults(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'token')
    settings = Settings(_env_file=None)
    assert settings.discovery_enabled is True
    assert settings.discovery_use_web is False
    assert settings.discovery_web_provider == 'disabled'
    assert settings.discovery_web_api_key is None
    assert settings.discovery_web_configured is False
    assert settings.discovery_web_active is False
