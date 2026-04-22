"""HTTP webhook handler for Bitrix stage changes."""

from __future__ import annotations

import logging

from aiohttp import web
from aiogram import Bot
from sqlalchemy import select

from bot.config import get_settings
from bot.database.models import Order, User
from bot.database.session import get_session

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


def _extract_value(payload: dict, key: str):
    if key in payload:
        return payload.get(key)
    data = payload.get("data")
    if isinstance(data, dict) and key in data:
        return data.get(key)
    fields = payload.get("fields")
    if isinstance(fields, dict) and key in fields:
        return fields.get(key)
    return None


@routes.post("/webhook/bitrix/stage_changed")
async def handle_stage_changed(request: web.Request) -> web.Response:
    payload = await request.json()
    settings = get_settings()

    raw_entity_type = _extract_value(payload, "entityTypeId")
    raw_item_id = _extract_value(payload, "itemId")
    raw_stage_id = _extract_value(payload, "stageId")
    raw_prev_stage_id = _extract_value(payload, "previousStageId")

    try:
        entity_type_id = int(raw_entity_type) if raw_entity_type is not None else None
    except (TypeError, ValueError):
        entity_type_id = None

    try:
        item_id = int(raw_item_id) if raw_item_id is not None else None
    except (TypeError, ValueError):
        item_id = None

    stage_id = str(raw_stage_id) if raw_stage_id is not None else "unknown"
    previous_stage_id = (
        str(raw_prev_stage_id) if raw_prev_stage_id is not None else "unknown"
    )

    logger.info(
        "Bitrix webhook received: entityTypeId=%s itemId=%s stageId=%s previousStageId=%s",
        entity_type_id,
        item_id,
        stage_id,
        previous_stage_id,
    )

    if entity_type_id != settings.BITRIX_ENTITY_TYPE_ID or item_id is None:
        return web.json_response({"status": "ok"})

    async with get_session() as session:
        result = await session.execute(
            select(Order).where(Order.bitrix_item_id == item_id)
        )
        order = result.scalar_one_or_none()
        if order is None:
            logger.warning("Order not found for bitrix_item_id=%s", item_id)
            return web.json_response({"status": "ok"})

        user_result = await session.execute(
            select(User).where(User.id == order.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            logger.warning("User not found for order_id=%s", order.id)
            return web.json_response({"status": "ok"})

    bot: Bot = request.app["bot"]
    await bot.send_message(
        chat_id=user.telegram_id,
        text=f"Ваш заказ №{order.id} перешёл в статус: {stage_id}",
    )
    return web.json_response({"status": "ok"})

