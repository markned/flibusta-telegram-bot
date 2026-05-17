from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib
from email_validator import EmailNotValidError, validate_email


class EmailConfigurationError(RuntimeError):
    pass


class EmailSender:
    def __init__(
        self,
        *,
        host: str | None,
        port: int,
        username: str | None,
        password: str | None,
        from_email: str | None,
        starttls: bool,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.starttls = starttls

    def validate_config(self) -> None:
        missing = [
            name
            for name, value in {
                "SMTP_HOST": self.host,
                "SMTP_USERNAME": self.username,
                "SMTP_PASSWORD": self.password,
                "SMTP_FROM_EMAIL": self.from_email,
            }.items()
            if not value
        ]
        if missing:
            raise EmailConfigurationError(f"Missing SMTP configuration: {', '.join(missing)}")
        validate_smtp_from_email(self.from_email or "")

    def build_message(
        self,
        *,
        to_email: str,
        subject: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> EmailMessage:
        self.validate_config()
        message = EmailMessage()
        message["From"] = self.from_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content("Book attached. Sent by Flibusta Telegram Bot.")
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename,
        )
        return message

    async def send_attachment(
        self,
        *,
        to_email: str,
        subject: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> None:
        message = self.build_message(
            to_email=to_email,
            subject=subject,
            filename=filename,
            content=content,
            content_type=content_type,
        )
        await aiosmtplib.send(
            message,
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            start_tls=self.starttls,
        )


def validate_smtp_from_email(value: str) -> str:
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise EmailConfigurationError("SMTP_FROM_EMAIL is not a valid e-mail address.") from exc
