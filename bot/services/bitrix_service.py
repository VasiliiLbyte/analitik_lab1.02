"""Bitrix24 integration: CRM deal creation after KP generation.

Design goals:
- Minimal coupling with the bot flow (best-effort, no hard failures)
- Async aiohttp client
- Safe logging (avoid leaking webhook URL)
- Optional company/contact find-or-create and link to deal
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
STAGE_NAMES = {
    "0": "Новое исследование",
    "1": "Забор проб",
    "2": "Ожидание проб",
    "3": "Выполнение исследования",
    "4": "Оформление протоколов",
    "5": "Готово",
    "6": "Отмена",
    "NEW": "Новое исследование",
    "PROBE": "Забор проб",
    "WAIT_PROBE": "Ожидание проб",
    "EXECUTION": "Выполнение исследования",
    "PROTOCOL": "Оформление протоколов",
    "DONE": "Готово",
    "CANCEL": "Отмена",
    "DT1062_10:NEW": "Новое исследование",
    "DT1062_10:PROBE": "Забор проб",
    "DT1062_10:WAIT_PROBE": "Ожидание проб",
    "DT1062_10:EXECUTION": "Выполнение исследования",
    "DT1062_10:PROTOCOL": "Оформление протоколов",
    "DT1062_10:DONE": "Готово",
    "DT1062_10:CANCEL": "Отмена",
    "Общая/Новое исследование": "Новое исследование",
    "Общая/Забор проб": "Забор проб",
    "Общая/Ожидание проб": "Ожидание проб",
    "Общая/Выполнение исследования": "Выполнение исследования",
    "Общая/Оформление протоколов": "Оформление протоколов",
    "Общая/Готово": "Готово",
    "Общая/Отмена": "Отмена",
    "Новое исследование": "Новое исследование",
    "Забор проб": "Забор проб",
    "Ожидание проб": "Ожидание проб",
    "Выполнение исследования": "Выполнение исследования",
    "Оформление протоколов": "Оформление протоколов",
    "Готово": "Готово",
    "Отмена": "Отмена",
}


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


def _normalize_stage_name(stage_name: str) -> str:
    value = (stage_name or "").strip()
    if not value:
        return "unknown"

    mapped = STAGE_NAMES.get(value)
    if mapped:
        return mapped

    if value.startswith("Общая/"):
        short_value = value.split("/", 1)[1].strip()
        return STAGE_NAMES.get(short_value, short_value or value)

    if ":" in value:
        suffix = value.rsplit(":", 1)[1].strip()
        if suffix:
            return STAGE_NAMES.get(suffix, suffix)

    return value


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


def _normalize_inn_digits(inn: str) -> str:
    return "".join(c for c in (inn or "") if c.isdigit())


def _normalize_phone_for_bitrix(phone: str) -> str:
    p = (phone or "").strip()
    if not p:
        return ""
    digits = "".join(c for c in p if c.isdigit())
    if not digits:
        return ""
    if p.strip().startswith("+") or digits.startswith("8") or digits.startswith("7"):
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        if digits.startswith("7") and len(digits) == 11:
            return f"+{digits}"
        if len(digits) >= 10:
            return f"+{digits}"
    return p


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _parse_fio_for_contact(fio: str) -> tuple[str, str, str]:
    """Return (NAME, SECOND_NAME, LAST_NAME) for Russian 'Фамилия Имя Отчество'."""
    parts = (fio or "").strip().split()
    if len(parts) >= 3:
        last, first, second = parts[0], parts[1], " ".join(parts[2:])
        return first, second, last
    if len(parts) == 2:
        return parts[1], "", parts[0]
    if len(parts) == 1:
        return parts[0], "", ""
    return "", "", ""


def _first_entity_id_from_list(data: dict[str, Any] | None) -> int | None:
    if not data:
        return None
    rows = data.get("result")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("ID") is not None:
            try:
                return int(row["ID"])
            except (TypeError, ValueError):
                continue
    return None


def _extract_bitrix_entity_id(data: dict[str, Any] | None) -> int | None:
    """Parse ID from crm.*.add / similar responses (result int or dict with ID)."""
    if not data:
        return None
    r = data.get("result")
    if isinstance(r, int):
        return r
    if isinstance(r, str):
        try:
            return int(r)
        except ValueError:
            return None
    if isinstance(r, dict) and r.get("ID") is not None:
        try:
            return int(r["ID"])
        except (TypeError, ValueError):
            return None
    return None


async def _bitrix_post_json(
    session: aiohttp.ClientSession,
    base_url: str,
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    url = f"{base_url}{method}"
    try:
        async with session.post(url, json=payload) as resp:
            raw = await resp.text()
            if resp.status != 200:
                logger.warning("Bitrix %s HTTP %s: %s", method, resp.status, raw[:500])
                return None
            data = json.loads(raw)
    except Exception:
        logger.exception("Bitrix %s request failed", method)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("error"):
        logger.warning(
            "Bitrix %s API error: %s %s",
            method,
            data.get("error"),
            data.get("error_description", ""),
        )
        return None
    return data


async def find_or_create_company(
    session: aiohttp.ClientSession,
    base_url: str,
    inn: str,
    company_name: str,
    client_data: dict[str, Any] | None = None,
) -> int | None:
    """Find company by INN (UF) or exact TITLE, else create. Returns ID or None."""
    settings = get_settings()
    inn_clean = _normalize_inn_digits(inn)
    name_clean = (company_name or "").strip()
    if name_clean in ("", "—"):
        name_clean = ""
    found_id: int | None = None
    new_id: int | None = None
    title_match = False
    will_create_new = False

    def _norm_title(value: str) -> str:
        return " ".join((value or "").split()).casefold()

    if not inn_clean and not name_clean:
        logger.info("Bitrix company skip: no INN and no company name")
        logger.info(
            "find_or_create_company: INN=%s, found_id=%s, title_match=%s, will_create_new=%s",
            inn_clean,
            found_id,
            title_match,
            will_create_new,
        )
        return None

    inn_uf = (settings.BITRIX_COMPANY_INN_UF or "").strip()

    if inn_clean and len(inn_clean) in (10, 12) and inn_uf:
        list_data = await _bitrix_post_json(
            session,
            base_url,
            "crm.company.list",
            {
                "filter": {inn_uf: inn_clean},
                "select": ["ID", "TITLE"],
                "start": 0,
            },
        )
        rows = (list_data or {}).get("result")
        if isinstance(rows, list) and rows:
            first_row = rows[0] if isinstance(rows[0], dict) else {}
            try:
                found_id = int(first_row.get("ID")) if first_row.get("ID") is not None else None
            except (TypeError, ValueError):
                found_id = None
            found_title = str(first_row.get("TITLE") or "").strip()
            title_match = _norm_title(found_title) == _norm_title(name_clean)
            if found_id is not None and title_match:
                will_create_new = False
                logger.info(
                    "find_or_create_company: INN=%s, found_id=%s, title_match=%s, will_create_new=%s",
                    inn_clean,
                    found_id,
                    title_match,
                    will_create_new,
                )
                return found_id
            if found_id is not None and not title_match:
                will_create_new = True
                logger.info(
                    "find_or_create_company: INN=%s, found_id=%s, title_match=%s, will_create_new=%s",
                    inn_clean,
                    found_id,
                    title_match,
                    will_create_new,
                )
        else:
            will_create_new = True

    if found_id is None and name_clean and not will_create_new:
        list_data = await _bitrix_post_json(
            session,
            base_url,
            "crm.company.list",
            {
                "filter": {"=TITLE": name_clean},
                "select": ["ID", "TITLE"],
                "start": 0,
            },
        )
        found_id = _first_entity_id_from_list(list_data)
        if found_id is not None:
            title_match = True
            will_create_new = False
            logger.info(
                "find_or_create_company: INN=%s, found_id=%s, title_match=%s, will_create_new=%s",
                inn_clean,
                found_id,
                title_match,
                will_create_new,
            )
            return found_id
        will_create_new = True

    title = name_clean or (f"Компания ИНН {inn_clean}" if inn_clean else "Клиент (Telegram)")
    fields: dict[str, Any] = {"TITLE": title}
    payload_data = client_data or {}
    kpp = str(payload_data.get("kpp") or "").strip()
    address = str(payload_data.get("address") or "").strip()
    phone = str(payload_data.get("phone") or payload_data.get("contact_info") or "").strip()
    email = str(payload_data.get("email") or "").strip()

    if address:
        fields["ADDRESS_LEGAL"] = address
    if phone:
        fields["PHONE"] = [{"VALUE": _normalize_phone_for_bitrix(phone), "VALUE_TYPE": "WORK"}]
    if email:
        fields["EMAIL"] = [{"VALUE": _normalize_email(email), "VALUE_TYPE": "WORK"}]
    fields["COMMENTS"] = (
        "Создано через Telegram-бот\n"
        f"ИНН: {inn}\n"
        f"КПП: {kpp}\n"
        f"Адрес: {address}"
    )

    logger.info(
        "find_or_create_company: create payload keys=%s title=%s has_address_legal=%s has_phone=%s has_email=%s",
        sorted(fields.keys()),
        title,
        bool(address),
        bool(phone),
        bool(email),
    )

    add_data = await _bitrix_post_json(
        session,
        base_url,
        "crm.company.add",
        {"fields": fields},
    )
    new_id = _extract_bitrix_entity_id(add_data)
    if new_id is not None:
        will_create_new = True
        logger.info(
            "find_or_create_company: INN=%s, found_id=%s, title_match=%s, will_create_new=%s",
            inn_clean,
            found_id,
            title_match,
            will_create_new,
        )
        return new_id

    logger.warning("Bitrix company add failed or returned no id")
    will_create_new = True
    logger.info(
        "find_or_create_company: INN=%s, found_id=%s, title_match=%s, will_create_new=%s",
        inn_clean,
        found_id,
        title_match,
        will_create_new,
    )
    return None


async def find_or_create_contact(
    session: aiohttp.ClientSession,
    base_url: str,
    fio: str,
    phone: str,
    email: str,
    force_create: bool = True,
) -> int | None:
    """Find contact by strict phone+email+fio combo, else create. Returns ID or None."""
    phone_norm = _normalize_phone_for_bitrix(phone)
    email_norm = _normalize_email(email)
    name, second_name, last_name = _parse_fio_for_contact(fio)
    found_id: int | None = None
    new_id: int | None = None

    def _extract_multifield_values(row: dict[str, Any], key: str) -> list[str]:
        values = row.get(key)
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            value = str(item.get("VALUE") or "").strip()
            if value:
                result.append(value)
        return result

    def _contact_matches(row: dict[str, Any]) -> bool:
        row_name = str(row.get("NAME") or "").strip()
        row_second = str(row.get("SECOND_NAME") or "").strip()
        row_last = str(row.get("LAST_NAME") or "").strip()
        if name and row_name != name:
            return False
        if second_name and row_second != second_name:
            return False
        if last_name and row_last != last_name:
            return False
        if phone_norm:
            row_phones = {
                _normalize_phone_for_bitrix(v) for v in _extract_multifield_values(row, "PHONE")
            }
            if phone_norm not in row_phones:
                return False
        if email_norm:
            row_emails = {_normalize_email(v) for v in _extract_multifield_values(row, "EMAIL")}
            if email_norm not in row_emails:
                return False
        return True

    strict_filter: dict[str, Any] = {}
    if phone_norm:
        strict_filter["=PHONE"] = phone_norm
    if email_norm:
        strict_filter["=EMAIL"] = email_norm
    if last_name:
        strict_filter["=LAST_NAME"] = last_name
    if name:
        strict_filter["=NAME"] = name
    if second_name:
        strict_filter["=SECOND_NAME"] = second_name

    if strict_filter:
        list_data = await _bitrix_post_json(
            session,
            base_url,
            "crm.contact.list",
            {
                "filter": strict_filter,
                "select": ["ID", "NAME", "SECOND_NAME", "LAST_NAME", "PHONE", "EMAIL"],
                "start": 0,
            },
        )
        rows = (list_data or {}).get("result")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if _contact_matches(row):
                    try:
                        found_id = int(row.get("ID"))
                    except (TypeError, ValueError):
                        found_id = None
                    if found_id is not None:
                        logger.info(
                            "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
                            found_id,
                            new_id,
                        )
                        return found_id

    if not force_create and not name and not last_name and not phone_norm and not email_norm:
        logger.info("Bitrix contact skip create: no name/phone/email")
        logger.info(
            "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
            found_id,
            new_id,
        )
        return None

    if not force_create and not strict_filter:
        logger.info("Bitrix contact skip create: strict search data is empty")
        logger.info(
            "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
            found_id,
            new_id,
        )
        return None

    fields: dict[str, Any] = {}
    if last_name:
        fields["LAST_NAME"] = last_name
    if name:
        fields["NAME"] = name
    if second_name:
        fields["SECOND_NAME"] = second_name
    if phone_norm:
        fields["PHONE"] = [{"VALUE": phone_norm, "VALUE_TYPE": "WORK"}]
    if email_norm:
        fields["EMAIL"] = [{"VALUE": email_norm, "VALUE_TYPE": "WORK"}]

    if not fields:
        logger.info("Bitrix contact skip create: no data to create contact")
        logger.info(
            "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
            found_id,
            new_id,
        )
        return None

    add_data = await _bitrix_post_json(
        session,
        base_url,
        "crm.contact.add",
        {"fields": fields},
    )
    new_id = _extract_bitrix_entity_id(add_data)
    if new_id is not None:
        logger.info(
            "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
            found_id,
            new_id,
        )
        return new_id

    logger.warning("Bitrix contact add failed or returned no id")
    logger.info(
        "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
        found_id,
        new_id,
    )
    return None


async def create_lab_item(
    client_data: dict,
    cart_items: list,
    total_sum: float,
    kp_number: str | None = None,
) -> int | None:
    """Create Bitrix24 deal (crm.deal.add) with optional company/contact link.

    Returns created deal id on success, otherwise None.
    Never raises (best-effort).
    """
    settings = get_settings()
    logger.info("=== BITRIX CREATE START ===")
    logger.info("Webhook URL: %s", settings.BITRIX_WEBHOOK_URL)
    logger.info("assignedId: %s", settings.BITRIX_ASSIGNED_ID)
    logger.info("observers: %s", settings.BITRIX_OBSERVERS)

    webhook = (settings.BITRIX_WEBHOOK_URL or "").strip()
    if not webhook:
        logger.info("Bitrix webhook not configured, skipping item creation")
        return None

    base = webhook if webhook.endswith("/") else f"{webhook}/"
    deal_url = f"{base}crm.deal.add"

    date_str = datetime.date.today().strftime("%d.%m.%Y")
    primary_service = _pick_primary_service_name(cart_items)
    title = _build_title(primary_service, client_data, date_str)
    services_detailed = _build_services_detailed_list(cart_items)
    fallback_phone, fallback_email = _split_contact_info(client_data)
    phone = str(client_data.get("phone", "")).strip() or fallback_phone
    email = str(client_data.get("email", "")).strip() or fallback_email

    observers = [
        int(x) for x in settings.BITRIX_OBSERVERS.split(",") if x.strip()
    ]

    fio = str(client_data.get("fio", "") or client_data.get("contact_person", "")).strip() or "—"
    company_name = str(client_data.get("company_name", "")).strip() or "—"
    inn = str(client_data.get("inn", "")).strip() or "—"
    kpp = str(client_data.get("kpp", "")).strip() or "—"
    comments = (
        "Заказ из Telegram-бота\n"
        f"Клиент: {fio} / {company_name}\n"
        f"ИНН: {inn}   КПП: {kpp}\n"
        f"Телефон: {phone or '—'}   Email: {email or '—'}\n\n"
        "Услуги:\n"
        f"{services_detailed}\n\n"
        f"Итого: {_fmt_money(float(total_sum))} ₽\n"
        f"Данные клиента: {json.dumps(client_data, ensure_ascii=False)}"
    )

    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            company_id = await find_or_create_company(
                session,
                base,
                inn if inn != "—" else "",
                company_name if company_name != "—" else "",
                client_data=client_data,
            )
            contact_id = await find_or_create_contact(
                session,
                base,
                fio if fio != "—" else "",
                phone,
                email,
            )

            fields: dict[str, Any] = {
                "CATEGORY_ID": settings.BITRIX_CATEGORY_ID,
                "STAGE_ID": settings.BITRIX_INITIAL_STAGE,
                "TITLE": title,
                "OPPORTUNITY": float(total_sum),
                "CURRENCY_ID": "RUB",
                "ASSIGNED_BY_ID": settings.BITRIX_ASSIGNED_ID,
                "OBSERVERS": observers,
                "COMMENTS": comments,
            }
            if company_id is not None:
                fields["COMPANY_ID"] = company_id
            if contact_id is not None:
                fields["CONTACT_ID"] = contact_id

            payload = {"fields": fields}

            logger.info(
                "Bitrix request: crm.deal.add (category=%s stage=%s title=%s total=%s)",
                settings.BITRIX_CATEGORY_ID,
                settings.BITRIX_INITIAL_STAGE,
                title,
                _fmt_money(float(total_sum)),
            )

            async with session.post(deal_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not isinstance(data, dict):
                        logger.error("Bitrix deal.add unexpected response type: %s", type(data))
                        return None
                    logger.info(
                        "Bitrix SUCCESS: %s",
                        json.dumps(data, ensure_ascii=False, indent=2),
                    )
                else:
                    data = await resp.text()
                    logger.error("Bitrix ERROR %s: %s", resp.status, data)
                    return None

            result = data.get("result")
            if isinstance(result, dict):
                deal_id = result.get("ID")
            elif isinstance(result, (int, str)):
                deal_id = result
            else:
                deal_id = None
            if deal_id is None:
                logger.warning("Bitrix response missing deal id: %s", str(data)[:2000])
                return None

            try:
                return int(deal_id)
            except Exception:
                logger.warning("Bitrix deal id is not int-like: %r", deal_id)
                return None
    except Exception as e:
        logger.error("BITRIX CREATE FAILED", exc_info=True)
        logger.error("Full error: %s", str(e))
        return None


async def get_current_stage(item_id: int) -> str:
    """Fetch current Bitrix stage name for smart-process item."""
    settings = get_settings()
    webhook = (settings.BITRIX_WEBHOOK_URL or "").strip()
    if not webhook:
        logger.info("Bitrix webhook not configured, cannot fetch stage")
        return "unknown"

    base = webhook if webhook.endswith("/") else f"{webhook}/"
    url = f"{base}crm.item.get"
    payload = {
        "entityTypeId": settings.BITRIX_ENTITY_TYPE_ID,
        "id": int(item_id),
    }
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Bitrix stage ERROR %s: %s", resp.status, text)
                    return "unknown"
                data = await resp.json()
    except Exception:
        logger.exception("Bitrix stage request failed (item_id=%s)", item_id)
        return "unknown"

    item = ((data or {}).get("result") or {}).get("item") or {}
    stage_id = str(item.get("stageId") or "").strip()
    if not stage_id:
        return "unknown"
    return _normalize_stage_name(STAGE_NAMES.get(stage_id, stage_id))

