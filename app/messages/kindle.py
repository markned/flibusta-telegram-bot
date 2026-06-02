from html import escape
from app.services.kindle import mask_email


def kindle_setup_text(sender: str | None, provider: str = "custom") -> str:
    sender_text = f"<code>{escape(sender)}</code>" if sender else "Отправитель пока не настроен владельцем бота."
    provider_note = ""
    if provider == "gmail":
        provider_note = "\n\nОтправка идёт через Gmail SMTP владельца бота."
    elif provider == "google_workspace":
        provider_note = "\n\nОтправка идёт через Gmail/Google Workspace SMTP владельца бота."
    return (
        "<b>Как настроить Kindle</b>\n\n"
        "1. Узнай свой Kindle e-mail.\n"
        "Обычно он находится в Amazon: Content & Devices → Preferences → Personal Document Settings.\n\n"
        "2. Добавь отправителя бота в Approved Personal Document E-mail List:\n"
        f"{sender_text}\n\n"
        "3. Вернись сюда и нажми «📮 Сохранить Kindle e-mail».\n\n"
        "4. После этого в карточке книги нажимай «Отправить на Kindle»."
        f"{provider_note}"
    )


def kindle_home_text(settings, sender: str | None, provider: str = "custom") -> str:
    sender_text = f"<code>{escape(sender)}</code>" if sender else "Отправитель пока не настроен владельцем бота."
    if settings is None:
        return (
            "<b>Kindle</b>\n\n"
            "Статус: ❌ Не настроен\n\n"
            "Чтобы отправлять книги на Kindle, нужно сделать 2 шага:\n"
            "1. Сохранить твой Kindle e-mail.\n"
            "2. Добавить отправителя бота в Amazon Approved Personal Document E-mail List.\n\n"
            f"Отправитель:\n{sender_text}"
        )
    approved = "✅ подтверждён" if getattr(settings, "approved_sender_confirmed", False) else "⚠️ не подтверждён"
    return (
        "<b>Kindle</b>\n\n"
        "Статус: ✅ Настроен\n\n"
        f"Kindle address: {mask_email(settings.kindle_email)}\n"
        f"Формат: {escape(settings.preferred_kindle_format.upper())}\n"
        f"Отправитель: {sender_text}\n"
        f"Отправитель добавлен в Amazon: {approved}"
    )


def kindle_missing_email_text() -> str:
    return "Kindle ещё не настроен. Давай настроим за минуту."

def kindle_sent_text(): return "Отправлено на Kindle. Обычно книга появляется через несколько минут."
def kindle_file_too_large_text(): return "Файл слишком большой для отправки на Kindle по e-mail."
def kindle_delivery_failed_text(): return "Не удалось отправить книгу на Kindle. Попробуй позже."
