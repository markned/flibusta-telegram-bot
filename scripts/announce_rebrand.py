#!/usr/bin/env python3
"""Send the rebrand notice once to each approved user."""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.branding import REBRAND_ANNOUNCEMENT_ID, rebrand_announcement_text
from app.config import Settings
from app.repositories.access import AccessRepository
from app.repositories.announcements import AnnouncementsRepository
from app.repositories.db import Database


async def main() -> None:
    settings = Settings()
    db = Database(settings.database_path)
    await db.initialize()
    access = AccessRepository(db)
    announcements = AnnouncementsRepository(db)
    user_ids = await access.list_user_ids(status="approved")
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="Открыть Полку", callback_data="home"))
    bot = Bot(settings.telegram_bot_token)
    sent = skipped = failed = 0
    try:
        for user_id in user_ids:
            if await announcements.was_sent(user_id, REBRAND_ANNOUNCEMENT_ID):
                skipped += 1
                continue
            try:
                await bot.send_message(
                    user_id,
                    rebrand_announcement_text(),
                    reply_markup=keyboard.as_markup(),
                    parse_mode="HTML",
                )
            except (TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError):
                failed += 1
                continue
            await announcements.mark_sent(user_id, REBRAND_ANNOUNCEMENT_ID)
            sent += 1
            await asyncio.sleep(0.08)
    finally:
        await bot.session.close()
    print(f"Rebrand announcement complete: sent={sent} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
