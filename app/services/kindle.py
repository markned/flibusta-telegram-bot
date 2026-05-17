from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from email_validator import EmailNotValidError, validate_email

from app.flibusta import DownloadFormat, FlibustaClient, FlibustaError
from app.repositories.kindle_deliveries import KindleDeliveriesRepository
from app.repositories.kindle_settings import KindleSettingsRepository
from app.services.email_sender import EmailSender


class KindleError(RuntimeError):
    pass


class MissingKindleSettingsError(KindleError):
    pass


class KindleEmailValidationError(KindleError):
    pass


class KindleAttachmentTooLargeError(KindleError):
    pass


class KindleRateLimitError(KindleError):
    pass


class KindleFormatUnavailableError(KindleError):
    pass


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
        raise KindleEmailValidationError("Введите корректный Kindle e-mail.") from exc
    if not normalized.lower().endswith(("@kindle.com", "@free.kindle.com")):
        raise KindleEmailValidationError("Нужен адрес Kindle: @kindle.com или @free.kindle.com.")
    return normalized


def mask_email(value: str | None) -> str:
    if not value:
        return "не настроен"
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if local else f"***@{domain}"


def choose_best_format(formats: Iterable[DownloadFormat], preferred: str | None) -> DownloadFormat:
    by_code = {item.code: item for item in formats}
    for code in [preferred, "epub", "fb2", "txt", "pdf"]:
        if code and code in by_code:
            return by_code[code]
    raise KindleFormatUnavailableError("Для этой книги нет подходящего формата для Kindle.")


class KindleService:
    def __init__(
        self,
        *,
        flibusta: FlibustaClient,
        settings_repo: KindleSettingsRepository,
        deliveries_repo: KindleDeliveriesRepository,
        email_sender: EmailSender,
        max_attachment_bytes: int,
        default_format: str,
        send_rate_limit_per_hour: int,
    ):
        self.flibusta = flibusta
        self.settings_repo = settings_repo
        self.deliveries_repo = deliveries_repo
        self.email_sender = email_sender
        self.max_attachment_bytes = max_attachment_bytes
        self.default_format = default_format
        self.send_rate_limit_per_hour = send_rate_limit_per_hour

    async def send_book_to_kindle(self, user_id: int, book_id: str) -> KindleSendResult:
        settings = await self.settings_repo.get(user_id)
        if settings is None or not settings.send_to_kindle_enabled:
            raise MissingKindleSettingsError("Kindle e-mail не настроен.")
        if await self.deliveries_repo.count_recent_for_rate_limit(user_id) >= self.send_rate_limit_per_hour:
            raise KindleRateLimitError("Слишком много отправок за час. Попробуйте позже.")

        delivery_id = await self.deliveries_repo.create(user_id, book_id)
        try:
            await self.deliveries_repo.update(delivery_id, status="downloading")
            details = await self.flibusta.details(book_id)
            target = choose_best_format(
                details.formats,
                settings.preferred_kindle_format or self.default_format,
            )
            try:
                content, filename, content_type = await self.flibusta.download(
                    target.url,
                    max_bytes=self.max_attachment_bytes,
                )
            except FlibustaError as exc:
                if "больше лимита" in str(exc).lower():
                    raise KindleAttachmentTooLargeError("Файл слишком большой для отправки на Kindle.") from exc
                raise
            size = len(content)
            if size > self.max_attachment_bytes:
                raise KindleAttachmentTooLargeError("Файл слишком большой для отправки на Kindle.")
            await self.deliveries_repo.update(
                delivery_id,
                status="downloaded",
                title=details.title,
                format=target.code,
                filename=filename,
                file_size_bytes=size,
            )
            await self.deliveries_repo.update(delivery_id, status="sending")
            await self.email_sender.send_attachment(
                to_email=settings.kindle_email,
                subject=details.title,
                filename=filename,
                content=content,
                content_type=content_type,
            )
            await self.deliveries_repo.update(delivery_id, status="sent")
            return KindleSendResult(details.title, filename, target.code, size)
        except Exception as exc:
            await self.deliveries_repo.update(delivery_id, status="failed", error=str(exc))
            raise
