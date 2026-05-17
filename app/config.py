from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    flibusta_base_url: HttpUrl = Field(default="https://flibusta.is", alias="FLIBUSTA_BASE_URL")
    http_proxy: str | None = Field(default=None, alias="HTTP_PROXY")
    telegram_proxy: str | None = Field(default=None, alias="TELEGRAM_PROXY")
    request_timeout_seconds: float = Field(default=25, alias="REQUEST_TIMEOUT_SECONDS")
    flibusta_retries: int = Field(default=4, alias="FLIBUSTA_RETRIES")
    flibusta_retry_delay_seconds: float = Field(default=2, alias="FLIBUSTA_RETRY_DELAY_SECONDS")
    flibusta_max_redirects: int = Field(default=8, alias="FLIBUSTA_MAX_REDIRECTS")
    telegram_request_timeout_seconds: float = Field(
        default=90,
        alias="TELEGRAM_REQUEST_TIMEOUT_SECONDS",
    )
    polling_retry_delay_seconds: float = Field(default=15, alias="POLLING_RETRY_DELAY_SECONDS")
    max_download_mb: int = Field(default=45, alias="MAX_DOWNLOAD_MB")
    telegram_max_upload_mb: int = Field(default=50, alias="TELEGRAM_MAX_UPLOAD_MB")
    search_results_limit: int = Field(default=40, alias="SEARCH_RESULTS_LIMIT")
    smtp_provider: str = Field(default="amazon_ses", alias="SMTP_PROVIDER")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from_email: str | None = Field(default=None, alias="SMTP_FROM_EMAIL")
    smtp_starttls: bool = Field(default=True, alias="SMTP_STARTTLS")
    kindle_max_attachment_mb: int = Field(default=28, alias="KINDLE_MAX_ATTACHMENT_MB")
    kindle_default_format: str = Field(default="epub", alias="KINDLE_DEFAULT_FORMAT")
    kindle_send_rate_limit_per_hour: int = Field(default=5, alias="KINDLE_SEND_RATE_LIMIT_PER_HOUR")
    database_path: str = Field(default="bot.db", alias="DATABASE_PATH")

    @property
    def base_url(self) -> str:
        return str(self.flibusta_base_url).rstrip("/")

    @property
    def normalized_http_proxy(self) -> str | None:
        return self.http_proxy or None

    @property
    def normalized_telegram_proxy(self) -> str | None:
        return self.telegram_proxy or None
