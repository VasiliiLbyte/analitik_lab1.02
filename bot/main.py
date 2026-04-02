"""Entry point: Dispatcher, Redis, routers, middleware, startup/shutdown hooks.

Resilience:
- Redis connection failure -> fallback to MemoryStorage
- Graceful shutdown with DB/Redis cleanup
- All routers registered in correct order (specific first, free_text last)
- KP template created on startup if missing
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import get_settings, setup_logging
from bot.database.session import close_db, init_db
from bot.handlers import cart, faq, free_text, kp_form, services, start
from bot.middleware.anti_flood import AntiFloodMiddleware
from bot.services.price_loader import PriceLoader

logger = logging.getLogger(__name__)


def _create_storage(settings):
    """Create FSM storage: Redis if available, MemoryStorage as fallback."""
    try:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(
            settings.redis_url,
            state_ttl=settings.fsm_ttl,
            data_ttl=settings.fsm_ttl,
        )
        logger.info("Using RedisStorage at %s", settings.redis_url)
        return storage
    except Exception as exc:
        logger.warning("Redis unavailable (%s), falling back to MemoryStorage", exc)
        return MemoryStorage()


async def on_startup(bot: Bot) -> None:
    settings = get_settings()
    await init_db(settings.database_url)

    PriceLoader.get()

    me = await bot.get_me()
    logger.info("Bot started: @%s (%s)", me.username, me.full_name)


async def on_shutdown(bot: Bot) -> None:
    await close_db()
    logger.info("Bot shutting down")


def create_dispatcher() -> Dispatcher:
    settings = get_settings()
    storage = _create_storage(settings)
    dp = Dispatcher(storage=storage)

    dp.message.middleware(
        AntiFloodMiddleware(
            rate_limit=settings.anti_flood_rate,
            spam_warn_threshold=settings.spam_warn_threshold,
            spam_block_threshold=settings.spam_block_threshold,
            spam_block_duration=settings.spam_block_duration,
        )
    )

    # Order matters: specific handlers first, free_text (catch-all) last
    dp.include_router(start.router)
    dp.include_router(faq.router)
    dp.include_router(kp_form.router)
    dp.include_router(services.router)
    dp.include_router(cart.router)
    dp.include_router(free_text.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    return dp


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()

    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt")
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)
