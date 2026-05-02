from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
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

    @property
    def base_url(self) -> str:
        return str(self.flibusta_base_url).rstrip("/")
