from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.config import settings, validate_settings, webhook_url
from app.db import init_db, reset_limits
from app.handlers import setup_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def daily_reset_task() -> None:
    while True:
        now = datetime.now()
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))
        await reset_limits()
        logger.info("Kunlik limitlar yangilandi")


def build_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    setup_handlers(dp)
    return dp


async def on_startup(bot: Bot) -> None:
    await init_db()
    asyncio.create_task(daily_reset_task())
    logger.info("Bot ishga tushdi")


async def run_polling() -> None:
    bot = build_bot()
    dp = build_dispatcher()
    await on_startup(bot)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


async def run_webhook() -> None:
    bot = build_bot()
    dp = build_dispatcher()
    await on_startup(bot)

    app = web.Application()
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret or None,
    )
    handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    url = webhook_url()
    await bot.set_webhook(
        url=url,
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=False,
        allowed_updates=dp.resolve_used_update_types(),
    )

    logger.info("Webhook rejimi yoqildi: %s", url)
    logger.info("Port ochildi: %s", settings.port)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await bot.delete_webhook(drop_pending_updates=False)
        await runner.cleanup()


async def main() -> None:
    validate_settings()
    if settings.use_webhook:
        await run_webhook()
    else:
        await run_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot to'xtatildi")
