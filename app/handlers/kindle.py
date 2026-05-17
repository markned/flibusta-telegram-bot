from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiosmtplib.errors import SMTPAuthenticationError, SMTPRecipientsRefused, SMTPResponseException

from app.repositories.kindle_settings import KindleSettingsRepository
from app.services.email_sender import EmailConfigurationError
from app.services.kindle import (
    KindleAttachmentTooLargeError,
    KindleEmailValidationError,
    KindleRateLimitError,
    KindleService,
    MissingKindleSettingsError,
    mask_email,
    validate_kindle_email,
)

logger = logging.getLogger(__name__)


def build_kindle_router(
    *,
    settings_repo: KindleSettingsRepository,
    kindle_service: KindleService,
    smtp_from_email: str | None,
    default_format: str,
) -> Router:
    router = Router()

    @router.message(Command("kindle_email"))
    async def kindle_email(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if not raw:
            await message.answer(
                "Сохрани Kindle e-mail так:\n"
                "<code>/kindle_email my_name_123@kindle.com</code>"
            )
            return
        try:
            normalized = validate_kindle_email(raw)
        except KindleEmailValidationError as exc:
            await message.answer(str(exc))
            return
        await settings_repo.upsert(message.from_user.id, normalized, preferred_format=default_format)
        await message.answer(f"Kindle e-mail сохранён: {mask_email(normalized)}")

    @router.message(Command("kindle_help"))
    async def kindle_help(message: Message) -> None:
        sender = smtp_from_email or "SMTP_FROM_EMAIL ещё не настроен администратором"
        await message.answer(
            "Как настроить отправку на Kindle:\n"
            "1. Найди свой Kindle e-mail в настройках Amazon Kindle.\n"
            f"2. Добавь адрес отправителя <code>{escape(sender)}</code> "
            "в Amazon Approved Personal Document E-mail List.\n"
            "3. Сохрани Kindle e-mail командой "
            "<code>/kindle_email my_name_123@kindle.com</code>.\n"
            "4. После этого используй кнопку «📤 Send to Kindle» в карточке книги.\n\n"
            f"Одобрить в Amazon нужно именно адрес отправителя: <code>{escape(sender)}</code>."
        )

    @router.message(Command("kindle_status"))
    async def kindle_status(message: Message) -> None:
        settings = await settings_repo.get(message.from_user.id)
        if settings is None:
            await message.answer(
                "Kindle e-mail пока не настроен.\n"
                "Добавь его командой <code>/kindle_email my_name_123@kindle.com</code>."
            )
            return
        sender = smtp_from_email or "не настроен"
        await message.answer(
            f"Kindle e-mail: {mask_email(settings.kindle_email)}\n"
            f"Предпочтительный формат: {escape(settings.preferred_kindle_format)}\n"
            f"Одобренный отправитель в Amazon: <code>{escape(sender)}</code>"
        )

    @router.message(Command("kindle_remove"))
    async def kindle_remove(message: Message) -> None:
        await settings_repo.delete(message.from_user.id)
        await message.answer("Kindle e-mail удалён.")

    @router.callback_query(F.data.startswith("kindle:"))
    async def send_to_kindle(callback: CallbackQuery) -> None:
        book_id = callback.data.split(":", 1)[1]
        await callback.answer()
        settings = await settings_repo.get(callback.from_user.id)
        if settings is None:
            await callback.message.answer(
                "Сначала настрой Kindle e-mail:\n"
                "<code>/kindle_email my_name_123@kindle.com</code>\n\n"
                "Подробности: /kindle_help"
            )
            return

        status = await callback.message.answer("Preparing file for Kindle…")
        try:
            await status.edit_text("Downloading EPUB…")
            await status.edit_text("Sending to Kindle…")
            await kindle_service.send_book_to_kindle(callback.from_user.id, book_id)
        except MissingKindleSettingsError:
            await status.edit_text(
                "Сначала настрой Kindle e-mail:\n"
                "<code>/kindle_email my_name_123@kindle.com</code>"
            )
        except KindleRateLimitError:
            await status.edit_text("Лимит отправок на Kindle за час исчерпан. Попробуй позже.")
        except KindleAttachmentTooLargeError:
            await status.edit_text("The file is too large to send to Kindle by e-mail. Try another format.")
        except SMTPAuthenticationError:
            logger.exception("Kindle SMTP authentication failed")
            await status.edit_text("Kindle sending is temporarily unavailable.")
        except EmailConfigurationError:
            logger.exception("Kindle SMTP configuration is incomplete")
            await status.edit_text("Kindle sending is temporarily unavailable.")
        except SMTPRecipientsRefused:
            logger.exception("Kindle SMTP recipient rejected")
            await status.edit_text(
                "Kindle e-mail delivery failed. Check your Kindle address and make sure "
                "the bot sender address is approved in Amazon."
            )
        except SMTPResponseException as exc:
            logger.exception("Kindle SMTP response error code=%s", exc.code)
            if exc.code in {552, 554}:
                await status.edit_text("The file is too large to send to Kindle by e-mail. Try another format.")
            else:
                await status.edit_text(
                    "Kindle e-mail delivery failed. Check your Kindle address and make sure "
                    "the bot sender address is approved in Amazon."
                )
        except Exception:
            logger.exception("Unexpected Kindle delivery failure")
            await status.edit_text("Kindle e-mail delivery failed. Try again later.")
        else:
            await status.edit_text("Sent to Kindle. It usually appears in a few minutes.")

    return router
