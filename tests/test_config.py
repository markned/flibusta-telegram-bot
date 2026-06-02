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


def test_smtp_gmail_provider_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("SMTP_PROVIDER", "gmail")
    monkeypatch.setenv("SMTP_USERNAME", "books@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "books@example.com")
    settings = Settings(_env_file=None)
    assert settings.smtp_provider_normalized == "gmail"
    assert settings.smtp_effective_host == "smtp.gmail.com"
    assert settings.smtp_effective_port == 587
    assert settings.smtp_effective_starttls is True
    assert settings.smtp_config_present is True
    assert settings.smtp_sender_domain == "example.com"


def test_smtp_custom_explicit_host_and_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("SMTP_PROVIDER", "disabled")
    monkeypatch.setenv("SMTP_HOST", "smtp.internal.example")
    monkeypatch.setenv("SMTP_USERNAME", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "books@example.com")
    settings = Settings(_env_file=None)
    assert settings.smtp_provider_normalized == "disabled"
    assert settings.smtp_effective_host == "smtp.internal.example"
    assert settings.smtp_config_present is False


def test_smtp_new_env_vars_load(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("SMTP_PROVIDER", "google_workspace")
    monkeypatch.setenv("SMTP_CUSTOM_DOMAIN", "example.com")
    monkeypatch.setenv("SMTP_DNS_CHECKS_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.smtp_provider_normalized == "google_workspace"
    assert settings.smtp_effective_host == "smtp.gmail.com"
    assert settings.smtp_custom_domain == "example.com"
    assert settings.smtp_dns_checks_enabled is True
