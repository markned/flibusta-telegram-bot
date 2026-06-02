from aiosmtplib.errors import SMTPAuthenticationError, SMTPRecipientsRefused, SMTPResponseException
from app.services.email_sender import EmailConfigurationError
import httpx


def classify_smtp_error(exc):
    if isinstance(exc, SMTPAuthenticationError):
        return "auth_error"
    if isinstance(exc, EmailConfigurationError):
        return "config_error"
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return "temporary_failure"
    if isinstance(exc, SMTPRecipientsRefused):
        return "recipient_rejected"
    if isinstance(exc, SMTPResponseException):
        if exc.code in {552, 554}:
            return "message_too_large"
        if exc.code in {421, 450, 451, 452, 454}:
            return "temporary_failure"
        if 500 <= exc.code < 600:
            return "sender_rejected"
    return "unknown_failure"


def smtp_user_message(cat):
    return {
        "auth_error": "SMTP не авторизовался. Если используется Gmail, проверь app password и двухэтапную проверку.",
        "config_error": "Отправка на Kindle пока не настроена владельцем бота.",
        "recipient_rejected": "Доставка не прошла. Проверь Kindle e-mail и добавь отправителя бота в Amazon.",
        "sender_rejected": "Доставка не прошла. Проверь Kindle e-mail и добавь отправителя бота в Amazon.",
        "message_too_large": "Файл слишком большой для отправки на Kindle по e-mail.",
        "throttled": "SMTP временно ограничивает отправку. Я попробую автоматически позже.",
        "temporary_failure": "SMTP временно недоступен. Я попробую автоматически позже.",
    }.get(cat, "Не удалось отправить книгу на Kindle. Попробуй позже.")


def is_transient(cat):
    return cat in {"throttled", "temporary_failure"}
