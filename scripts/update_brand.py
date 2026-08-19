#!/usr/bin/env python3
"""Apply the public Polka identity to the Telegram bot profile."""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputProfilePhotoStatic

from app.branding import BRAND_DESCRIPTION, BRAND_NAME, BRAND_SHORT_DESCRIPTION
from app.config import Settings


async def main() -> None:
    settings = Settings()
    avatar = Path("assets/polka-avatar.jpg")
    if not avatar.is_file():
        raise FileNotFoundError("assets/polka-avatar.jpg")
    bot = Bot(settings.telegram_bot_token)
    try:
        await bot.set_my_name(name=BRAND_NAME)
        await bot.set_my_description(description=BRAND_DESCRIPTION)
        await bot.set_my_short_description(short_description=BRAND_SHORT_DESCRIPTION)
        await bot.set_my_profile_photo(
            photo=InputProfilePhotoStatic(photo=FSInputFile(avatar))
        )
    finally:
        await bot.session.close()
    print("Telegram brand updated: name=yes description=yes avatar=yes")


if __name__ == "__main__":
    asyncio.run(main())
