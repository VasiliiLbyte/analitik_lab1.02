"""HTTP webhook handler for Bitrix stage changes."""

from __future__ import annotations

import json
import logging
import re

from aiohttp import web
from aiogram import Bot
from sqlalchemy import select

from bot.config import get_settings
from bot.database.models import Order, User
from bot.database.session import get_session

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()
_DYNAMIC_DOC_RE = re.compile(r"^DYNAMIC_(\d+)_(\d+)$")
STAGE_NAMES = {
    "DT1062_10:NEW": "Новое исследование",
    "DT1062_10:PROBE": "Забор проб",
    "DT1062_10:WAIT_PROBE": "Ожидание проб",
    "DT1062_10:EXECUTION": "Выполнение исследования",
    "DT1062_10:PROTOCOL": "Оформление протоколов",
    "DT1062_10:DONE": "Готово",
    "DT1062_10:CANCEL": "Отмена",
}


def _try_parse_json(value):
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_value(payload: dict, key: str):
    if key in payload:
        return payload.get(key)

    data = payload.get("data")
    parsed_data = _try_parse_json(data)
    if isinstance(parsed_data, dict) and key in parsed_data:
        return parsed_data.get(key)
    if isinstance(data, dict) and key in data:
        return data.get(key)

    fields = payload.get("fields")
    parsed_fields = _try_parse_json(fields)
    if isinstance(parsed_fields, dict) and key in parsed_fields:
        return parsed_fields.get(key)
    if isinstance(fields, dict) and key in fields:
        return fields.get(key)

    aliases = {
        "entityTypeId": [
            "event[data][ENTITY_TYPE_ID]",
            "data[ENTITY_TYPE_ID]",
            "ENTITY_TYPE_ID",
            "entity_type_id",
        ],
        "itemId": [
            "event[data][ID]",
            "event[data][FIELDS][ID]",
            "data[ID]",
            "ID",
            "item_id",
        ],
        "stageId": [
            "event[data][STAGE_ID]",
            "event[data][FIELDS][STAGE_ID]",
            "data[STAGE_ID]",
            "STAGE_ID",
            "stage_id",
        ],
        "previousStageId": [
            "event[data][PREVIOUS_STAGE_ID]",
            "event[data][FIELDS][PREVIOUS_STAGE_ID]",
            "data[PREVIOUS_STAGE_ID]",
            "PREVIOUS_STAGE_ID",
            "previous_stage_id",
        ],
    }
    for alias in aliases.get(key, []):
        if alias in payload:
            return payload.get(alias)

    return None


def _extract_from_document_id(payload: dict) -> tuple[int | None, int | None]:
    raw_value = (
        payload.get("document_id[2]")
        or payload.get("document_id.2")
        or payload.get("document_id_2")
    )
    if raw_value is None:
        document_id = payload.get("document_id")
        if isinstance(document_id, list) and len(document_id) >= 3:
            raw_value = document_id[2]
        elif isinstance(document_id, str):
            parsed = _try_parse_json(document_id)
            if isinstance(parsed, list) and len(parsed) >= 3:
                raw_value = parsed[2]

    if raw_value is None:
        return None, None

    match = _DYNAMIC_DOC_RE.match(str(raw_value).strip())
    if not match:
        return None, None

    try:
        return int(match.group(1)), int(match.group(2))
    except (TypeError, ValueError):
        return None, None


@routes.post("/webhook/bitrix/stage_changed")
async def handle_stage_changed(request: web.Request) -> web.Response:
    body_text = await request.text()
    logger.info("Bitrix webhook headers: %s", dict(request.headers))
    logger.info("Bitrix webhook body: %s", body_text)

    payload: dict = {}
    try:
        payload = await request.json()
    except Exception:
        try:
            form_data = await request.post()
            payload = dict(form_data)
        except Exception:
            logger.warning("Failed to parse webhook payload, fallback to raw text")

    settings = get_settings()

    raw_entity_type = _extract_value(payload, "entityTypeId")
    raw_item_id = _extract_value(payload, "itemId")
    raw_stage_id = _extract_value(payload, "stageId")
    raw_prev_stage_id = _extract_value(payload, "previousStageId")

    if raw_entity_type is None or raw_item_id is None:
        doc_entity_type_id, doc_item_id = _extract_from_document_id(payload)
        if raw_entity_type is None and doc_entity_type_id is not None:
            raw_entity_type = doc_entity_type_id
        if raw_item_id is None and doc_item_id is not None:
            raw_item_id = doc_item_id

    try:
        entity_type_id = int(raw_entity_type) if raw_entity_type is not None else None
    except (TypeError, ValueError):
        entity_type_id = None

    try:
        item_id = int(raw_item_id) if raw_item_id is not None else None
    except (TypeError, ValueError):
        item_id = None

    stage_id = str(raw_stage_id) if raw_stage_id is not None else "unknown"
    stage_name = STAGE_NAMES.get(stage_id, stage_id)
    previous_stage_id = (
        str(raw_prev_stage_id) if raw_prev_stage_id is not None else "unknown"
    )

    logger.info(
        (
            "Bitrix webhook parsed: raw_entityTypeId=%s raw_itemId=%s "
            "raw_stageId=%s raw_previousStageId=%s "
            "entityTypeId=%s itemId=%s stageId=%s previousStageId=%s"
        ),
        raw_entity_type,
        raw_item_id,
        raw_stage_id,
        raw_prev_stage_id,
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
        text=f"Ваш заказ №{order.id} перешёл в статус: {stage_name}",
    )
    return web.json_response({"status": "ok"})

