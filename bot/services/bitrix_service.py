"""Bitrix24 integration: create smart-process item after KP generation.

Design goals:
- Minimal coupling with the bot flow (best-effort, no hard failures)
- Async aiohttp client
- Safe logging (avoid leaking webhook URL)
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

import aiohttp
from num2words import num2words

from bot.config import get_settings

logger = logging.getLogger(__name__)


def _fmt_money(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ")


def _decline_rubles(n: int) -> str:
    last2 = n % 100
    last1 = n % 10
    if 11 <= last2 <= 19:
        return "рублей"
    if last1 == 1:
        return "рубль"
    if 2 <= last1 <= 4:
        return "рубля"
    return "рублей"


def _decline_kopecks(n: int) -> str:
    last2 = n % 100
    last1 = n % 10
    if 11 <= last2 <= 19:
        return "копеек"
    if last1 == 1:
        return "копейка"
    if 2 <= last1 <= 4:
        return "копейки"
    return "копеек"


def _amount_in_words(amount: float) -> str:
    rubles = int(amount)
    kopecks = round((amount - rubles) * 100)
    rubles_text = num2words(rubles, lang="ru").capitalize()
    return f"{rubles_text} {_decline_rubles(rubles)} {kopecks:02d} {_decline_kopecks(kopecks)}"


def _pick_primary_service_name(cart_items: list) -> str:
    if not cart_items:
        return "услуги"

    def _line_total(item: Any) -> float:
        try:
            return float(item.unit_price) * int(item.quantity)
        except Exception:
            return 0.0

    primary = max(cart_items, key=_line_total)
    name = getattr(primary, "service_name", None) or getattr(primary, "name", None) or "услуги"
    return str(name).strip()[:80] or "услуги"


def _build_services_short_list(cart_items: list, limit: int = 6) -> str:
    parts: list[str] = []
    for item in cart_items[:limit]:
        name = getattr(item, "service_name", None) or getattr(item, "name", None) or ""
        qty = getattr(item, "quantity", None)
        unit = getattr(item, "unit", None) or "шт"
        try:
            qty_i = int(qty) if qty is not None else 1
        except Exception:
            qty_i = 1
        label = str(name).strip()
        if not label:
            continue
        parts.append(f"{label} ×{qty_i} {unit}")

    more = max(0, len(cart_items) - limit)
    if more:
        parts.append(f"…и ещё {more}")
    return "; ".join(parts) if parts else "—"


def _split_contact_info(client_data: dict) -> tuple[str, str]:
    contact_info = str(client_data.get("contact_info", "")).strip()
    if "@" in contact_info:
        return "", contact_info
    return contact_info, ""


def _build_services_detailed_list(cart_items: list) -> str:
    if not cart_items:
        return "—"

    lines: list[str] = []
    for idx, item in enumerate(cart_items, 1):
        name = str(getattr(item, "service_name", "")).strip() or "Услуга"
        unit = str(getattr(item, "unit", "шт")).strip() or "шт"
        try:
            quantity = int(getattr(item, "quantity", 1))
        except Exception:
            quantity = 1
        try:
            unit_price = float(getattr(item, "unit_price", 0.0))
        except Exception:
            unit_price = 0.0
        line_total = round(unit_price * quantity, 2)
        lines.append(
            f"{idx}. {name} — {quantity} {unit} × {_fmt_money(unit_price)} ₽ = {_fmt_money(line_total)} ₽"
        )
    return "\n".join(lines)


def _build_title(
    primary_service: str,
    client_data: dict,
    date_str: str,
) -> str:
    company = (client_data.get("company_name") or "").strip()
    fio = (client_data.get("fio") or client_data.get("contact_person") or "").strip()
    who = company or fio or "Клиент"
    return f"Исследование {primary_service} — {who} — {date_str}"


async def create_lab_item(
    client_data: dict,
    cart_items: list,
    total_sum: float,
    kp_number: str | None = None,
) -> int | None:
    """Create Bitrix24 smart-process item (entityTypeId=1062 by default).

    Returns created item id on success, otherwise None.
    Never raises (best-effort).
    """
    settings = get_settings()
    logger.info("=== BITRIX CREATE START ===")
    logger.info("Webhook URL: %s", settings.BITRIX_WEBHOOK_URL)
    logger.info("entityTypeId: %s", settings.BITRIX_ENTITY_TYPE_ID)
    logger.info("assignedId: %s", settings.BITRIX_ASSIGNED_ID)
    logger.info("observers: %s", settings.BITRIX_OBSERVERS)

    webhook = (settings.BITRIX_WEBHOOK_URL or "").strip()
    if not webhook:
        logger.info("Bitrix webhook not configured, skipping item creation")
        return None

    base = webhook if webhook.endswith("/") else f"{webhook}/"
    url = f"{base}crm.item.add"

    date_str = datetime.date.today().strftime("%d.%m.%Y")
    primary_service = _pick_primary_service_name(cart_items)
    title = _build_title(primary_service, client_data, date_str)
    services_short = _build_services_short_list(cart_items)
    services_detailed = _build_services_detailed_list(cart_items)
    total_words = _amount_in_words(float(total_sum))
    created_at = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    fallback_phone, fallback_email = _split_contact_info(client_data)
    phone = str(client_data.get("phone", "")).strip() or fallback_phone
    email = str(client_data.get("email", "")).strip() or fallback_email

    observers = [
        int(x) for x in settings.BITRIX_OBSERVERS.split(",") if x.strip()
    ]

    fio = str(client_data.get("fio", "")).strip() or "—"
    company_name = str(client_data.get("company_name", "")).strip() or "—"
    inn = str(client_data.get("inn", "")).strip() or "—"
    kpp = str(client_data.get("kpp", "")).strip() or "—"
    kp_text = f"КП №{kp_number}" if kp_number else "КП №—"
    service_list = services_detailed
    today_iso = datetime.date.today().isoformat()
    research_period_iso = (
        datetime.date.today() + datetime.timedelta(days=14)
    ).isoformat()
    uf_description = (
        "Заказ из Telegram-бота\n"
        f"Клиент: {fio} / {company_name}\n"
        f"ИНН: {inn}   КПП: {kpp}\n"
        f"Телефон: {phone or ''}\n"
        f"Email: {email or ''}\n\n"
        "Услуги:\n"
        f"{service_list}\n\n"
        f"Итого: {_fmt_money(float(total_sum))} ₽"
    )
    comments = (
        "Заказ из Telegram-бота\n"
        f"Клиент: {fio} / {company_name}\n"
        f"ИНН: {inn}   КПП: {kpp}\n"
        f"Телефон: {phone or '—'}   Email: {email or '—'}\n\n"
        "Услуги:\n"
        f"{services_detailed}\n\n"
        f"Итого: {_fmt_money(float(total_sum))} ₽ ({total_words})\n"
        f"{kp_text}\n"
        f"Кратко: {services_short}\n"
        f"Создано автоматически {created_at}\n"
        f"Данные клиента: {json.dumps(client_data, ensure_ascii=False)}"
    )

    payload = {
        "entityTypeId": settings.BITRIX_ENTITY_TYPE_ID,
        "fields": {
            "title": title,
            "stageId": None,  # Let Bitrix set default stage ("Новое исследование")
            "assignedById": settings.BITRIX_ASSIGNED_ID,
            "observers": observers,
            "comments": comments,
            "ufCrm7Description": uf_description,
            "ufCrm7Getdate": today_iso,
            "ufCrm7Researchperiod": research_period_iso,
            "ufCrm7Rescomment": (
                f"{kp_text} создано в Telegram-боте "
                f"{datetime.datetime.now():%d.%m.%Y %H:%M}"
            ),
        },
    }

    logger.info(
        "Bitrix request: crm.item.add (entityTypeId=%s title=%s total=%s)",
        settings.BITRIX_ENTITY_TYPE_ID,
        title,
        _fmt_money(float(total_sum)),
    )

    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(
                        "Bitrix SUCCESS: %s",
                        json.dumps(data, ensure_ascii=False, indent=2),
                    )
                else:
                    data = await resp.text()
                    logger.error("Bitrix ERROR %s: %s", resp.status, data)
                    return None

        item = ((data or {}).get("result") or {}).get("item") or {}
        item_id = item.get("id")
        if item_id is None:
            logger.warning("Bitrix response missing item id: %s", str(data)[:2000])
            return None

        try:
            return int(item_id)
        except Exception:
            logger.warning("Bitrix item id is not int-like: %r", item_id)
            return None
    except Exception as e:
        logger.error("BITRIX CREATE FAILED", exc_info=True)
        logger.error("Full error: %s", str(e))
        return None

