from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from email_validator import EmailNotValidError, validate_email

from app.flibusta import DownloadFormat, FlibustaClient, FlibustaError
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.services.conversion import ConversionNotAvailableError, ConversionService
from app.services.email_sender import EmailSender

ProgressCallback = Callable[[str], Awaitable[None]]


class KindleError(RuntimeError):
    pass


class KindleSettingsMissingError(KindleError):
    pass


class KindleEmailInvalidError(KindleError):
    pass


class KindleFileTooLargeError(KindleError):
    pass


class KindleRateLimitError(KindleError):
    pass


class KindleSmtpConfigError(KindleError):
    pass


class KindleDeliveryRejectedError(KindleError):
    pass


class KindleConversionNotAvailableError(KindleError):
    pass


class KindleFormatUnavailableError(KindleError):
    pass


# First-iteration compatibility aliases.
MissingKindleSettingsError = KindleSettingsMissingError
KindleEmailValidationError = KindleEmailInvalidError
KindleAttachmentTooLargeError = KindleFileTooLargeError


@dataclass(frozen=True)
class KindleSendResult:
    title: str
    filename: str
    format: str
    file_size_bytes: int


def validate_kindle_email(value: str) -> str:
    try:
        normalized = validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise KindleEmailInvalidError("Enter a valid Kindle e-mail.") from exc
    domain = normalized.rsplit("@", 1)[-1].lower()
    if domain not in {"kindle.com", "free.kindle.com"}:
        raise KindleEmailInvalidError("Use a Kindle address ending in @kindle.com or @free.kindle.com.")
    return normalized


def mask_email(value: str | None) -> str:
    if not value or "@" not in value:
        return "not configured"
    local, domain = value.split("@", 1)
    return f"{local[:1] or '*'}***@{domain}"


def sanitize_filename(filename: str, fallback: str = "book", max_length: int = 120) -> str:
    cleaned = re.sub(r"[\r\n]+", " ", filename or "")
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    suffix = Path(cleaned).suffix
    stem = Path(cleaned).stem if cleaned else fallback
    stem = stem.strip().strip(".") or fallback
    allowed_stem_len = max(1, max_length - len(suffix))
    return f"{stem[:allowed_stem_len]}{suffix}"[:max_length]


def choose_best_format(formats: Iterable[DownloadFormat], preferred: str | None) -> DownloadFormat:
    by_code = {item.code: item for item in formats}
    for code in [preferred, "epub", "fb2", "txt", "pdf"]:
        if code and code in by_code:
            return by_code[code]
    raise KindleFormatUnavailableError("No Kindle-compatible format is available for this book.")


class KindleService:
    def __init__(
        self,
        *,
        flibusta: FlibustaClient,
        settings_repo: KindleSettingsRepository,
        deliveries_repo: KindleDeliveriesRepository,
        email_sender: EmailSender,
        conversion_service: ConversionService,
        max_attachment_bytes: int,
        default_format: str,
        send_rate_limit_per_hour: int,
        enable_conversion: bool,
        conversion_target_format: str,
    ):
        self.flibusta = flibusta
        self.settings_repo = settings_repo
        self.deliveries_repo = deliveries_repo
        self.email_sender = email_sender
        self.conversion_service = conversion_service
        self.max_attachment_bytes = max_attachment_bytes
        self.default_format = default_format
        self.send_rate_limit_per_hour = send_rate_limit_per_hour
        self.enable_conversion = enable_conversion
        self.conversion_target_format = conversion_target_format

    async def create_queued_delivery(self, user_id: int, book_id: str) -> int:
        settings = await self.settings_repo.get(user_id)
        if settings is None or not settings.send_to_kindle_enabled:
            raise KindleSettingsMissingError("Kindle e-mail is not configured.")
        if await self.deliveries_repo.count_recent_for_user(user_id) >= self.send_rate_limit_per_hour:
            raise KindleRateLimitError("Kindle send rate limit exceeded.")
        return await self.deliveries_repo.create_delivery(user_id, book_id, status="queued")

    async def process_delivery(
        self,
        *,
        delivery_id: int,
        user_id: int,
        book_id: str,
        on_progress: ProgressCallback | None = None,
    ) -> KindleSendResult:
        settings = await self.settings_repo.get(user_id)
        if settings is None or not settings.send_to_kindle_enabled:
            raise KindleSettingsMissingError("Kindle e-mail is not configured.")
        try:
            details = await self.flibusta.details(book_id)
            target = choose_best_format(details.formats, settings.preferred_kindle_format or self.default_format)
            await self.deliveries_repo.update_status(
                delivery_id,
                "downloading",
                title=details.title,
                format=target.code,
            )
            await _progress(on_progress, f"Downloading {target.code.upper()}…")
            try:
                content, filename, content_type = await self.flibusta.download(
                    target.url,
                    max_bytes=self.max_attachment_bytes,
                )
            except FlibustaError as exc:
                if "больше лимита" in str(exc).lower():
                    raise KindleFileTooLargeError("File exceeds Kindle attachment limit.") from exc
                raise
            if len(content) > self.max_attachment_bytes:
                raise KindleFileTooLargeError("File exceeds Kindle attachment limit.")
            filename = sanitize_filename(filename)
            await self.deliveries_repo.update_status(
                delivery_id,
                "downloaded",
                filename=filename,
                file_size_bytes=len(content),
            )
            if self.enable_conversion:
                await self.deliveries_repo.update_status(delivery_id, "converting")
                await _progress(on_progress, "Preparing e-mail…")
                try:
                    converted = await self.conversion_service.maybe_convert_for_kindle(
                        content,
                        filename,
                        target.code,
                        self.conversion_target_format,
                    )
                except ConversionNotAvailableError as exc:
                    raise KindleConversionNotAvailableError(str(exc)) from exc
                content, filename, target_code = converted.content, converted.filename, converted.format
            else:
                target_code = target.code
                await _progress(on_progress, "Preparing e-mail…")
            await self.deliveries_repo.update_status(
                delivery_id,
                "sending",
                format=target_code,
                filename=filename,
                file_size_bytes=len(content),
            )
            await _progress(on_progress, "Sending to Kindle…")
            await self.email_sender.send_attachment(
                to_email=settings.kindle_email,
                subject=details.title,
                filename=filename,
                content=content,
                content_type=content_type,
            )
            await self.deliveries_repo.update_status(delivery_id, "sent")
            return KindleSendResult(details.title, filename, target_code, len(content))
        except Exception as exc:
            await self.deliveries_repo.mark_failed(delivery_id, redact_sensitive_text(str(exc)))
            raise

    async def send_book_to_kindle(self, user_id: int, book_id: str) -> KindleSendResult:
        delivery_id = await self.create_queued_delivery(user_id, book_id)
        return await self.process_delivery(delivery_id=delivery_id, user_id=user_id, book_id=book_id)


async def _progress(callback: ProgressCallback | None, text: str) -> None:
    if callback is not None:
        await callback(text)


def redact_sensitive_text(value: str) -> str:
    return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email redacted]", value)
