"""Anti-flood middleware: rate-limiting, repeat message detection, spam blocking.

Protection scenarios covered:
- 1 message per 1.5 seconds rate limit
- Duplicate message counter (warn after 5, block after 10 repeats)
- Temporary 5-minute ban for persistent spammers
- Blocks during active spam cooldown
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)


class AntiFloodMiddleware(BaseMiddleware):
    def __init__(
        self,
        rate_limit: float = 1.5,
        spam_warn_threshold: int = 5,
        spam_block_threshold: int = 10,
        spam_block_duration: int = 300,
    ) -> None:
        self.rate_limit = rate_limit
        self.spam_warn_threshold = spam_warn_threshold
        self.spam_block_threshold = spam_block_threshold
        self.spam_block_duration = spam_block_duration

        self._last_message_time: Dict[int, float] = {}
        self._repeat_counters: Dict[int, tuple[str, int]] = {}
        self._blocked_until: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id
        now = time.monotonic()

        # --- Blocked users ---
        blocked_until = self._blocked_until.get(user_id, 0)
        if now < blocked_until:
            remaining = int(blocked_until - now)
            logger.warning("Spam block active for user %d (%ds left)", user_id, remaining)
            await event.answer(
                f"⏳ Слишком много сообщений. Подождите {remaining} сек."
            )
            return None

        if now >= blocked_until and user_id in self._blocked_until:
            del self._blocked_until[user_id]
            self._repeat_counters.pop(user_id, None)

        # --- Rate limit ---
        last_time = self._last_message_time.get(user_id, 0)
        if now - last_time < self.rate_limit:
            logger.debug("Rate limit hit for user %d", user_id)
            return None
        self._last_message_time[user_id] = now

        # --- Repeat detection ---
        text = (event.text or "").strip()
        if text:
            msg_hash = hashlib.md5(text.encode()).hexdigest()
            prev_hash, count = self._repeat_counters.get(user_id, ("", 0))

            if msg_hash == prev_hash:
                count += 1
            else:
                count = 1

            self._repeat_counters[user_id] = (msg_hash, count)

            if count >= self.spam_block_threshold:
                self._blocked_until[user_id] = now + self.spam_block_duration
                logger.warning(
                    "User %d blocked for %ds (repeated %d times)",
                    user_id, self.spam_block_duration, count,
                )
                await event.answer(
                    f"🚫 Вы заблокированы на {self.spam_block_duration // 60} мин. "
                    "за повторяющиеся сообщения."
                )
                return None

            if count >= self.spam_warn_threshold:
                await event.answer(
                    "⚠️ Пожалуйста, не повторяйте одно и то же сообщение."
                )
                return None

        return await handler(event, data)
