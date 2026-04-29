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
import os
import socket
import sys
from urllib.parse import urlparse

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import get_settings, setup_logging
from bot.database.session import close_db, init_db
from bot.handlers import bitrix_webhook, cart, faq, free_text, kp_form, services, start
from bot.middleware.anti_flood import AntiFloodMiddleware
from bot.services.bitrix_service import BitrixService
from bot.services.price_loader import PriceLoader
from bot.services.runtime_lock import RuntimeLock

logger = logging.getLogger(__name__)
bitrix_service = BitrixService()


def _create_storage(settings):
    """Create FSM storage: Redis if available, MemoryStorage as fallback."""
    try:
        parsed = urlparse(settings.redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        # Fast preflight check: if TCP connection fails, do not use RedisStorage.
        with socket.create_connection((host, port), timeout=1.0):
            pass

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

    me = await bot.get_me()
    logger.info("Bot started: @%s (%s)", me.username, me.full_name)
    logger.info("Starting Bitrix product synchronization...")
    sync_result = await bitrix_service.sync_products()
    logger.info(
        "Product sync completed: loaded=%s added=%s updated=%s path=%s",
        sync_result["loaded"],
        sync_result["added"],
        sync_result["updated"],
        sync_result["path"],
    )

    price_loader = PriceLoader(use_synced=True)
    price_loader.load()
    PriceLoader._instance = price_loader


async def on_shutdown(bot: Bot) -> None:
    await close_db()
    logger.info("Bot shutting down")


async def _start_bitrix_webhook_server(bot: Bot) -> web.AppRunner:
    host = os.getenv("BITRIX_WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("BITRIX_WEBHOOK_PORT", "8080"))

    app = web.Application()
    app["bot"] = bot
    app.add_routes(bitrix_webhook.routes)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    logger.info(
        "Bitrix webhook server started: http://%s:%s/webhook/bitrix/stage_changed",
        host,
        port,
    )
    return runner


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
    logger.info("Application version: %s", settings.app_version)

    runtime_lock: RuntimeLock | None = None
    if settings.runtime_lock_enabled:
        runtime_lock = RuntimeLock(settings.runtime_lock_path)
        if not runtime_lock.acquire():
            logger.error(
                "Another bot instance is already running (lock: %s). "
                "Stop existing process before restart.",
                settings.runtime_lock_path,
            )
            sys.exit(2)

    session = AiohttpSession(proxy=settings.telegram_proxy) if settings.telegram_proxy else AiohttpSession()
    if settings.telegram_proxy:
        logger.info("Using Telegram proxy: %s", settings.telegram_proxy)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    dp = create_dispatcher()
    webhook_runner: web.AppRunner | None = None

    logger.info("Starting polling...")
    try:
        webhook_runner = await _start_bitrix_webhook_server(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if webhook_runner is not None:
            await webhook_runner.cleanup()
        await bot.session.close()
        if runtime_lock is not None:
            runtime_lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt")
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)
