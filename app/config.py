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
    smtp_provider: str = Field(default="custom", alias="SMTP_PROVIDER")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from_email: str | None = Field(default=None, alias="SMTP_FROM_EMAIL")
    smtp_starttls: bool = Field(default=True, alias="SMTP_STARTTLS")
    smtp_custom_domain: str | None = Field(default=None, alias="SMTP_CUSTOM_DOMAIN")
    smtp_dns_checks_enabled: bool = Field(default=False, alias="SMTP_DNS_CHECKS_ENABLED")
    kindle_max_attachment_mb: int = Field(default=28, alias="KINDLE_MAX_ATTACHMENT_MB")
    kindle_default_format: str = Field(default="epub", alias="KINDLE_DEFAULT_FORMAT")
    kindle_send_rate_limit_per_hour: int = Field(default=5, alias="KINDLE_SEND_RATE_LIMIT_PER_HOUR")
    kindle_worker_concurrency: int = Field(default=1, alias="KINDLE_WORKER_CONCURRENCY")
    kindle_user_concurrency: int = Field(default=1, alias="KINDLE_USER_CONCURRENCY")
    kindle_enable_conversion: bool = Field(default=False, alias="KINDLE_ENABLE_CONVERSION")
    kindle_conversion_target_format: str = Field(default="epub", alias="KINDLE_CONVERSION_TARGET_FORMAT")
    kindle_max_job_attempts: int = Field(default=3, alias="KINDLE_MAX_JOB_ATTEMPTS")
    kindle_retry_base_delay_seconds: int = Field(default=10, alias="KINDLE_RETRY_BASE_DELAY_SECONDS")
    kindle_delivery_log_retention_days: int = Field(default=90, alias="KINDLE_DELIVERY_LOG_RETENTION_DAYS")

    kindle_metadata_polish_enabled: bool = Field(default=True, alias="KINDLE_METADATA_POLISH_ENABLED")
    kindle_metadata_require_calibre: bool = Field(default=False, alias="KINDLE_METADATA_REQUIRE_CALIBRE")
    kindle_metadata_tool: str = Field(default="ebook-meta", alias="KINDLE_METADATA_TOOL")
    kindle_metadata_timeout_seconds: float = Field(default=30, alias="KINDLE_METADATA_TIMEOUT_SECONDS")
    kindle_embed_cover_enabled: bool = Field(default=True, alias="KINDLE_EMBED_COVER_ENABLED")
    kindle_filename_template: str = Field(default="{author} - {title}", alias="KINDLE_FILENAME_TEMPLATE")
    kindle_strict_metadata_title_author: bool = Field(default=True, alias="KINDLE_STRICT_METADATA_TITLE_AUTHOR")
    admin_export_include_full_emails: bool = Field(default=False, alias="ADMIN_EXPORT_INCLUDE_FULL_EMAILS")
    database_path: str = Field(default="bot.db", alias="DATABASE_PATH")
    admin_user_ids: str = Field(default="", alias="ADMIN_USER_IDS")
    cache_enabled: bool = Field(default=True, alias="CACHE_ENABLED")
    cache_book_search_ttl_seconds: int = Field(default=1800, alias="CACHE_BOOK_SEARCH_TTL_SECONDS")
    cache_author_search_ttl_seconds: int = Field(default=1800, alias="CACHE_AUTHOR_SEARCH_TTL_SECONDS")
    cache_smart_search_ttl_seconds: int = Field(default=1800, alias="CACHE_SMART_SEARCH_TTL_SECONDS")
    cache_book_details_ttl_seconds: int = Field(default=21600, alias="CACHE_BOOK_DETAILS_TTL_SECONDS")
    cache_author_books_ttl_seconds: int = Field(default=21600, alias="CACHE_AUTHOR_BOOKS_TTL_SECONDS")
    book_annotation_max_chars: int = Field(default=1200, alias="BOOK_ANNOTATION_MAX_CHARS")

    book_cover_ui_enabled: bool = Field(default=True, alias="BOOK_COVER_UI_ENABLED")
    book_cover_send_as_photo: bool = Field(default=True, alias="BOOK_COVER_SEND_AS_PHOTO")
    book_cover_fallback_to_text: bool = Field(default=True, alias="BOOK_COVER_FALLBACK_TO_TEXT")
    cover_card_caption_max_chars: int = Field(default=900, alias="COVER_CARD_CAPTION_MAX_CHARS")
    cover_lookup_enabled: bool = Field(default=True, alias="COVER_LOOKUP_ENABLED")
    cover_provider_order: str = Field(default="flibusta,openlibrary,google_books", alias="COVER_PROVIDER_ORDER")
    cover_lookup_timeout_seconds: float = Field(default=6, alias="COVER_LOOKUP_TIMEOUT_SECONDS")
    cover_cache_ttl_seconds: int = Field(default=604800, alias="COVER_CACHE_TTL_SECONDS")
    cover_negative_cache_ttl_seconds: int = Field(default=86400, alias="COVER_NEGATIVE_CACHE_TTL_SECONDS")
    cover_max_download_mb: int = Field(default=3, alias="COVER_MAX_DOWNLOAD_MB")
    cover_min_width: int = Field(default=300, alias="COVER_MIN_WIDTH")
    cover_min_height: int = Field(default=400, alias="COVER_MIN_HEIGHT")
    cover_min_confidence: float = Field(default=0.72, alias="COVER_MIN_CONFIDENCE")
    google_books_api_key: str | None = Field(default=None, alias="GOOGLE_BOOKS_API_KEY")
    search_rate_limit_per_minute: int = Field(default=20, alias="SEARCH_RATE_LIMIT_PER_MINUTE")
    download_rate_limit_per_hour: int = Field(default=30, alias="DOWNLOAD_RATE_LIMIT_PER_HOUR")
    access_control_enabled: bool = Field(default=True, alias="ACCESS_CONTROL_ENABLED")
    ai_enabled: bool = Field(default=False, alias="AI_ENABLED")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    ai_model: str = Field(default="gpt-5-nano", alias="AI_MODEL")
    ai_intent_cache_ttl_seconds: int = Field(default=86400, alias="AI_INTENT_CACHE_TTL_SECONDS")
    ai_recommendation_max_queries_used: int = Field(default=6, alias="AI_RECOMMENDATION_MAX_QUERIES_USED")
    ai_recommendation_target_results: int = Field(default=8, alias="AI_RECOMMENDATION_TARGET_RESULTS")
    ai_recommendation_min_results: int = Field(default=5, alias="AI_RECOMMENDATION_MIN_RESULTS")
    ai_recommendation_max_details: int = Field(default=6, alias="AI_RECOMMENDATION_MAX_DETAILS")
    ai_recommendation_books_per_query: int = Field(default=3, alias="AI_RECOMMENDATION_BOOKS_PER_QUERY")
    discovery_enabled: bool = Field(default=True, alias="DISCOVERY_ENABLED")
    discovery_use_web: bool = Field(default=False, alias="DISCOVERY_USE_WEB")
    discovery_web_provider: str = Field(default="disabled", alias="DISCOVERY_WEB_PROVIDER")
    discovery_web_api_key: str | None = Field(default=None, alias="DISCOVERY_WEB_API_KEY")
    discovery_max_web_results: int = Field(default=5, alias="DISCOVERY_MAX_WEB_RESULTS")
    discovery_max_web_snippet_chars: int = Field(default=500, alias="DISCOVERY_MAX_WEB_SNIPPET_CHARS")
    discovery_max_book_ideas: int = Field(default=12, alias="DISCOVERY_MAX_BOOK_IDEAS")
    discovery_max_flibusta_checks: int = Field(default=8, alias="DISCOVERY_MAX_FLIBUSTA_CHECKS")
    discovery_max_final_results: int = Field(default=10, alias="DISCOVERY_MAX_FINAL_RESULTS")
    discovery_cache_ttl_seconds: int = Field(default=604800, alias="DISCOVERY_CACHE_TTL_SECONDS")
    discovery_user_daily_limit: int = Field(default=5, alias="DISCOVERY_USER_DAILY_LIMIT")
    discovery_global_daily_limit: int = Field(default=50, alias="DISCOVERY_GLOBAL_DAILY_LIMIT")
    discovery_concurrency: int = Field(default=1, alias="DISCOVERY_CONCURRENCY")
    discovery_model: str | None = Field(default=None, alias="DISCOVERY_MODEL")
    discovery_timeout_seconds: float = Field(default=15, alias="DISCOVERY_TIMEOUT_SECONDS")
    recommendation_confirmation_ttl_seconds: int = Field(default=900, alias="RECOMMENDATION_CONFIRMATION_TTL_SECONDS")
    recommendation_confirmation_required: bool = Field(default=True, alias="RECOMMENDATION_CONFIRMATION_REQUIRED")
    literary_sources_enabled: bool = Field(default=False, alias="LITERARY_SOURCES_ENABLED")
    literary_source_provider: str = Field(default="disabled", alias="LITERARY_SOURCE_PROVIDER")

    @property
    def base_url(self) -> str:
        return str(self.flibusta_base_url).rstrip("/")

    @property
    def normalized_http_proxy(self) -> str | None:
        return self.http_proxy or None

    @property
    def normalized_telegram_proxy(self) -> str | None:
        return self.telegram_proxy or None


    @property
    def smtp_provider_normalized(self) -> str:
        provider = (self.smtp_provider or "custom").strip().lower()
        allowed = {"custom", "gmail", "google_workspace", "zoho", "brevo", "mailgun", "amazon_ses", "disabled"}
        return provider if provider in allowed else "custom"

    @property
    def smtp_effective_host(self) -> str | None:
        if self.smtp_host:
            return self.smtp_host
        return {
            "gmail": "smtp.gmail.com",
            "google_workspace": "smtp.gmail.com",
            "zoho": "smtp.zoho.com",
            "brevo": "smtp-relay.brevo.com",
        }.get(self.smtp_provider_normalized)

    @property
    def smtp_effective_port(self) -> int:
        return self.smtp_port or 587

    @property
    def smtp_effective_starttls(self) -> bool:
        if self.smtp_provider_normalized in {"gmail", "google_workspace", "zoho", "brevo", "mailgun"}:
            return True
        return self.smtp_starttls

    @property
    def smtp_config_present(self) -> bool:
        return bool(
            self.smtp_provider_normalized != "disabled"
            and self.smtp_effective_host
            and self.smtp_username
            and self.smtp_password
            and self.smtp_from_email
        )

    @property
    def smtp_sender_domain(self) -> str | None:
        if not self.smtp_from_email or "@" not in self.smtp_from_email:
            return None
        return self.smtp_from_email.rsplit("@", 1)[1].lower()


    @property
    def cover_provider_order_list(self) -> list[str]:
        allowed = {"flibusta", "openlibrary", "google_books", "disabled"}
        values = [item.strip().lower() for item in self.cover_provider_order.split(",") if item.strip()]
        return [item for item in values if item in allowed] or ["flibusta"]

    @property
    def admin_ids(self) -> set[int]:
        return {int(item.strip()) for item in self.admin_user_ids.split(",") if item.strip().isdigit()}

    @property
    def discovery_web_configured(self) -> bool:
        return self.discovery_web_provider == "tavily" and bool(self.discovery_web_api_key)

    @property
    def discovery_web_active(self) -> bool:
        return self.discovery_enabled and self.discovery_use_web and self.discovery_web_configured
