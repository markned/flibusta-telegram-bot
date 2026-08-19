"""Single source of truth for the public product identity."""

BRAND_NAME = "Полка"
BRAND_DESCRIPTION = (
    "Личная библиотека: найди книгу, скачай её или отправь на Kindle и PocketBook."
)
BRAND_SHORT_DESCRIPTION = "Книги для Kindle, PocketBook и других устройств."
BRAND_EMAIL_FROM_NAME = "Полка"
REBRAND_ANNOUNCEMENT_ID = "polka-rebrand-2026-08"


def rebrand_announcement_text() -> str:
    return (
        "<b>Теперь мы — Полка</b>\n\n"
        "Библиотека получила новое короткое имя и новый знак. "
        "Всё знакомое осталось на месте: поиск, избранное, история, "
        "Kindle, PocketBook и веб-версия.\n\n"
        "Просто напиши название книги или автора — Полка найдёт остальное."
    )
