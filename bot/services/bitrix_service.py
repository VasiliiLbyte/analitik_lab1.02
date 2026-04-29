"""Bitrix24 integration: CRM deal creation and product sync."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
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


class BitrixService:
    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings or get_settings()

    @staticmethod
    def _fmt_money(amount: float) -> str:
        return f"{amount:,.2f}".replace(",", " ")

    @staticmethod
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

    @staticmethod
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

    def _amount_in_words(self, amount: float) -> str:
        rubles = int(amount)
        kopecks = round((amount - rubles) * 100)
        rubles_text = num2words(rubles, lang="ru").capitalize()
        return f"{rubles_text} {self._decline_rubles(rubles)} {kopecks:02d} {self._decline_kopecks(kopecks)}"

    @staticmethod
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

    @staticmethod
    def _split_contact_info(client_data: dict) -> tuple[str, str]:
        contact_info = str(client_data.get("contact_info", "")).strip()
        if "@" in contact_info:
            return "", contact_info
        return contact_info, ""

    def _build_services_detailed_list(self, cart_items: list) -> str:
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
                f"{idx}. {name} — {quantity} {unit} × {self._fmt_money(unit_price)} ₽ = {self._fmt_money(line_total)} ₽"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_title(primary_service: str, client_data: dict, date_str: str) -> str:
        company = (client_data.get("company_name") or "").strip()
        fio = (client_data.get("fio") or client_data.get("contact_person") or "").strip()
        who = company or fio or "Клиент"
        return f"Исследование {primary_service} — {who} — {date_str}"

    @staticmethod
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

    @staticmethod
    def _normalize_inn_digits(inn: str) -> str:
        return "".join(c for c in (inn or "") if c.isdigit())

    @staticmethod
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

    @staticmethod
    def _normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    @staticmethod
    def _parse_fio_for_contact(fio: str) -> tuple[str, str, str]:
        parts = (fio or "").strip().split()
        if len(parts) >= 3:
            last, first, second = parts[0], parts[1], " ".join(parts[2:])
            return first, second, last
        if len(parts) == 2:
            return parts[1], "", parts[0]
        if len(parts) == 1:
            return parts[0], "", ""
        return "", "", ""

    @staticmethod
    def _extract_multifield_values(row: dict[str, Any], key: str) -> list[str]:
        field = row.get(key)
        values: list[str] = []
        if isinstance(field, list):
            for item in field:
                if isinstance(item, dict):
                    value = str(item.get("VALUE", "")).strip()
                    if value:
                        values.append(value)
        return values

    @staticmethod
    def _extract_bitrix_entity_id(data: dict[str, Any] | None) -> int | None:
        if not data:
            return None
        result = data.get("result")
        if isinstance(result, int):
            return result
        if isinstance(result, str):
            try:
                return int(result)
            except ValueError:
                return None
        if isinstance(result, dict):
            raw_id = result.get("ID")
            try:
                return int(raw_id) if raw_id is not None else None
            except (TypeError, ValueError):
                return None
        return None

    async def _post_json(
        self,
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
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        inn: str,
        company_name: str,
        client_data: dict[str, Any] | None = None,
    ) -> int | None:
        inn_clean = self._normalize_inn_digits(inn)
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

        inn_uf = (self.settings.BITRIX_COMPANY_INN_UF or "").strip()

        if inn_clean and len(inn_clean) in (10, 12) and inn_uf:
            list_data = await self._post_json(
                session,
                base_url,
                "crm.company.list",
                {
                    "filter": {f"={inn_uf}": inn_clean},
                    "select": ["ID", "TITLE", inn_uf],
                    "start": 0,
                },
            )
            rows = (list_data or {}).get("result")
            if isinstance(rows, list):
                if name_clean:
                    target_title = _norm_title(name_clean)
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        row_title = _norm_title(str(row.get("TITLE", "")))
                        if row_title == target_title:
                            title_match = True
                            try:
                                found_id = int(row.get("ID"))
                            except (TypeError, ValueError):
                                found_id = None
                            if found_id is not None:
                                logger.info(
                                    "find_or_create_company: searched by INN=%s, found=%s, created new=%s",
                                    inn_clean,
                                    found_id,
                                    new_id,
                                )
                                return found_id
                else:
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        try:
                            found_id = int(row.get("ID"))
                        except (TypeError, ValueError):
                            found_id = None
                        if found_id is not None:
                            logger.info(
                                "find_or_create_company: searched by INN=%s, found=%s, created new=%s",
                                inn_clean,
                                found_id,
                                new_id,
                            )
                            return found_id

        if name_clean and found_id is None:
            list_data = await self._post_json(
                session,
                base_url,
                "crm.company.list",
                {"filter": {"=TITLE": name_clean}, "select": ["ID", "TITLE"], "start": 0},
            )
            rows = (list_data or {}).get("result")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if _norm_title(str(row.get("TITLE", ""))) != _norm_title(name_clean):
                        continue
                    try:
                        found_id = int(row.get("ID"))
                    except (TypeError, ValueError):
                        found_id = None
                    if found_id is not None:
                        logger.info(
                            "find_or_create_company: searched by INN=%s, found=%s, created new=%s",
                            inn_clean,
                            found_id,
                            new_id,
                        )
                        return found_id

        payload_data = client_data or {}
        kpp = str(payload_data.get("kpp", "")).strip() or "—"
        address = str(payload_data.get("address", "")).strip() or "—"
        phone, email = self._split_contact_info(payload_data)
        phone = str(payload_data.get("phone", "")).strip() or phone
        email = str(payload_data.get("email", "")).strip() or email

        will_create_new = True
        fields: dict[str, Any] = {
            "TITLE": name_clean or "Клиент из Telegram",
            "ADDRESS_LEGAL": address if address != "—" else "",
            "COMMENTS": (
                "Создано через Telegram-бот\n"
                f"ИНН: {inn_clean or '—'}\n"
                f"КПП: {kpp}\n"
                f"Адрес: {address}"
            ),
        }
        phone_norm = self._normalize_phone_for_bitrix(phone)
        if phone_norm:
            fields["PHONE"] = [{"VALUE": phone_norm, "VALUE_TYPE": "WORK"}]
        email_norm = self._normalize_email(email)
        if email_norm:
            fields["EMAIL"] = [{"VALUE": email_norm, "VALUE_TYPE": "WORK"}]

        logger.info("Bitrix company create payload keys: %s", list(fields.keys()))
        add_data = await self._post_json(session, base_url, "crm.company.add", {"fields": fields})
        new_id = self._extract_bitrix_entity_id(add_data)
        logger.info(
            "find_or_create_company: searched by INN=%s, found=%s, created new=%s",
            inn_clean,
            found_id,
            new_id,
        )
        return new_id

    async def find_or_create_contact(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        fio: str,
        phone: str,
        email: str,
        force_create: bool = True,
    ) -> int | None:
        found_id: int | None = None
        new_id: int | None = None
        phone_norm = self._normalize_phone_for_bitrix(phone)
        email_norm = self._normalize_email(email)
        name, second_name, last_name = self._parse_fio_for_contact(fio)

        def _contact_matches(row: dict[str, Any]) -> bool:
            if last_name and str(row.get("LAST_NAME", "")).strip() != last_name:
                return False
            if name and str(row.get("NAME", "")).strip() != name:
                return False
            if second_name and str(row.get("SECOND_NAME", "")).strip() != second_name:
                return False
            if phone_norm:
                row_phones = {
                    self._normalize_phone_for_bitrix(v) for v in self._extract_multifield_values(row, "PHONE")
                }
                if phone_norm not in row_phones:
                    return False
            if email_norm:
                row_emails = {self._normalize_email(v) for v in self._extract_multifield_values(row, "EMAIL")}
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
            list_data = await self._post_json(
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
            logger.info(
                "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
                found_id,
                new_id,
            )
            return None
        if not force_create and not strict_filter:
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
            logger.info(
                "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
                found_id,
                new_id,
            )
            return None

        add_data = await self._post_json(session, base_url, "crm.contact.add", {"fields": fields})
        new_id = self._extract_bitrix_entity_id(add_data)
        logger.info(
            "find_or_create_contact: searched by phone/email/fio, found=%s, created new=%s",
            found_id,
            new_id,
        )
        return new_id

    async def create_lab_item(
        self,
        client_data: dict,
        cart_items: list,
        total_sum: float,
        kp_number: str | None = None,
    ) -> int | None:
        webhook = (self.settings.BITRIX_WEBHOOK_URL or "").strip()
        if not webhook:
            logger.info("Bitrix webhook not configured, skipping item creation")
            return None
        base = webhook if webhook.endswith("/") else f"{webhook}/"
        deal_url = f"{base}crm.deal.add"
        date_str = datetime.date.today().strftime("%d.%m.%Y")
        primary_service = self._pick_primary_service_name(cart_items)
        title = self._build_title(primary_service, client_data, date_str)
        services_detailed = self._build_services_detailed_list(cart_items)
        fallback_phone, fallback_email = self._split_contact_info(client_data)
        phone = str(client_data.get("phone", "")).strip() or fallback_phone
        email = str(client_data.get("email", "")).strip() or fallback_email
        observers = [int(x) for x in self.settings.BITRIX_OBSERVERS.split(",") if x.strip()]
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
            f"Итого: {self._fmt_money(float(total_sum))} ₽\n"
            f"Данные клиента: {json.dumps(client_data, ensure_ascii=False)}"
        )
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                company_id = await self.find_or_create_company(
                    session,
                    base,
                    inn if inn != "—" else "",
                    company_name if company_name != "—" else "",
                    client_data=client_data,
                )
                contact_id = await self.find_or_create_contact(
                    session,
                    base,
                    fio if fio != "—" else "",
                    phone,
                    email,
                )
                fields: dict[str, Any] = {
                    "CATEGORY_ID": self.settings.BITRIX_CATEGORY_ID,
                    "STAGE_ID": self.settings.BITRIX_INITIAL_STAGE,
                    "TITLE": title,
                    "OPPORTUNITY": float(total_sum),
                    "CURRENCY_ID": "RUB",
                    "ASSIGNED_BY_ID": self.settings.BITRIX_ASSIGNED_ID,
                    "OBSERVERS": observers,
                    "COMMENTS": comments,
                }
                if company_id is not None:
                    fields["COMPANY_ID"] = company_id
                if contact_id is not None:
                    fields["CONTACT_ID"] = contact_id
                async with session.post(deal_url, json={"fields": fields}) as resp:
                    if resp.status != 200:
                        data = await resp.text()
                        logger.error("Bitrix ERROR %s: %s", resp.status, data)
                        return None
                    data = await resp.json()
            result = data.get("result")
            if isinstance(result, dict):
                deal_id = result.get("ID")
            elif isinstance(result, (int, str)):
                deal_id = result
            else:
                deal_id = None
            if deal_id is None:
                return None
            try:
                return int(deal_id)
            except Exception:
                return None
        except Exception:
            logger.exception("BITRIX CREATE FAILED")
            return None

    async def get_current_stage(self, item_id: int) -> str:
        webhook = (self.settings.BITRIX_WEBHOOK_URL or "").strip()
        if not webhook:
            return "unknown"
        base = webhook if webhook.endswith("/") else f"{webhook}/"
        url = f"{base}crm.item.get"
        payload = {"entityTypeId": self.settings.BITRIX_ENTITY_TYPE_ID, "id": int(item_id)}
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        return "unknown"
                    data = await resp.json()
        except Exception:
            logger.exception("Bitrix stage request failed (item_id=%s)", item_id)
            return "unknown"
        item = ((data or {}).get("result") or {}).get("item") or {}
        stage_id = str(item.get("stageId") or "").strip()
        if not stage_id:
            return "unknown"
        return self._normalize_stage_name(STAGE_NAMES.get(stage_id, stage_id))

    async def sync_products(self) -> dict[str, Any]:
        """Синхронизирует все товары/услуги из Bitrix24 и сохраняет в prices_synced.json."""
        webhook = (self.settings.BITRIX_WEBHOOK_URL or "").strip()
        if not webhook:
            logger.warning("sync_products skipped: BITRIX_WEBHOOK_URL is empty")
            return {"loaded": 0, "added": 0, "updated": 0, "total": 0, "path": ""}

        base = webhook if webhook.endswith("/") else f"{webhook}/"
        section_method = "crm.productsection.list"
        product_method = "crm.product.list"
        start: int | str = 0
        loaded = 0
        page = 0
        max_pages = 500
        seen_starts: set[int | str] = set()

        synced: dict[str, dict[str, Any]] = {}
        output_path = Path(__file__).resolve().parent.parent / "data" / "prices_synced.json"
        existing: dict[str, dict[str, Any]] = {}
        root_section_id = int(getattr(self.settings, "BITRIX_PRODUCTS_ROOT_SECTION_ID", 0) or 0)
        section_names: dict[str, str] = {}
        allowed_section_ids: set[str] = set()

        if output_path.exists():
            try:
                existing_raw = json.loads(output_path.read_text(encoding="utf-8"))
                if isinstance(existing_raw, dict):
                    existing = {
                        str(k): v for k, v in existing_raw.items() if isinstance(v, dict)
                    }
            except Exception:
                logger.exception("sync_products: failed to parse existing %s", output_path)

        logger.info("sync_products: start loading products from Bitrix")

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1) Load all product sections and keep only the configured branch (root + children).
            sections_all: list[dict[str, Any]] = []
            section_start: int | str = 0
            section_seen_starts: set[int | str] = set()
            section_page = 0
            while True:
                if section_start in section_seen_starts:
                    logger.warning("sync_products: section paging stop due to repeated start=%s", section_start)
                    break
                section_seen_starts.add(section_start)
                section_page += 1
                if section_page > max_pages:
                    logger.warning("sync_products: section paging stop by max pages=%s", max_pages)
                    break

                section_data = await self._post_json(
                    session,
                    base,
                    section_method,
                    {
                        "filter": {},
                        "select": ["ID", "NAME", "SECTION_ID", "IBLOCK_SECTION_ID"],
                        "start": section_start,
                    },
                )
                if not section_data:
                    break
                section_rows = section_data.get("result")
                if not isinstance(section_rows, list) or not section_rows:
                    break
                for row in section_rows:
                    if isinstance(row, dict):
                        sections_all.append(row)

                section_next = section_data.get("next")
                if section_next is None:
                    break
                section_start = section_next

            children_by_parent: dict[str, set[str]] = {}
            for row in sections_all:
                sid = str(row.get("ID") or "").strip()
                if not sid:
                    continue
                sname = str(row.get("NAME") or "").strip() or f"Раздел {sid}"
                section_names[sid] = sname
                parent_raw = row.get("SECTION_ID")
                if parent_raw in (None, ""):
                    parent_raw = row.get("IBLOCK_SECTION_ID")
                parent_id = str(parent_raw or "").strip()
                if parent_id:
                    children_by_parent.setdefault(parent_id, set()).add(sid)

            if root_section_id > 0:
                root_sid = str(root_section_id)
                stack = [root_sid]
                while stack:
                    current = stack.pop()
                    if current in allowed_section_ids:
                        continue
                    allowed_section_ids.add(current)
                    for child in children_by_parent.get(current, set()):
                        if child not in allowed_section_ids:
                            stack.append(child)
            else:
                allowed_section_ids = set(section_names.keys())

            logger.info(
                "sync_products: sections loaded total=%s allowed_in_branch=%s root=%s",
                len(section_names),
                len(allowed_section_ids),
                root_section_id,
            )

            # 2) Load products with paging.
            start = 0
            seen_starts.clear()
            page = 0
            while True:
                if start in seen_starts:
                    logger.warning("sync_products: stop due to repeated start=%s", start)
                    break
                seen_starts.add(start)
                page += 1
                if page > max_pages:
                    logger.warning("sync_products: stop by max pages=%s", max_pages)
                    break

                product_filter: dict[str, Any] = {"ACTIVE": "Y"}
                if root_section_id > 0:
                    product_filter["SECTION_ID"] = root_section_id
                    product_filter["INCLUDE_SUBSECTIONS"] = "Y"

                payload = {
                    "filter": product_filter,
                    "select": ["ID", "NAME", "PRICE", "MEASURE_NAME", "ACTIVE", "SECTION_ID"],
                    "start": start,
                }
                data = await self._post_json(session, base, product_method, payload)
                if not data:
                    logger.warning("sync_products: empty response at start=%s", start)
                    break

                rows = data.get("result")
                if not isinstance(rows, list) or not rows:
                    logger.info("sync_products: no more products at start=%s", start)
                    break

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("ACTIVE", "")).upper() != "Y":
                        continue
                    service_id = str(row.get("ID") or "").strip()
                    if not service_id:
                        continue
                    section_id = str(row.get("SECTION_ID") or "").strip()
                    if allowed_section_ids and section_id and section_id not in allowed_section_ids:
                        continue
                    if root_section_id > 0 and not section_id:
                        continue
                    name = str(row.get("NAME") or "").strip()
                    try:
                        price = float(row.get("PRICE") or 0.0)
                    except (TypeError, ValueError):
                        price = 0.0
                    unit = str(row.get("MEASURE_NAME") or "").strip() or "шт"
                    category_id = section_id or "uncategorized"
                    category_name = (
                        section_names.get(category_id)
                        if category_id != "uncategorized"
                        else "Без категории"
                    ) or "Без категории"
                    synced[service_id] = {
                        "name": name,
                        "price": price,
                        "unit": unit,
                        "category_id": category_id,
                        "category_name": category_name,
                    }
                    loaded += 1

                logger.info(
                    "sync_products: loaded page start=%s count=%s total_loaded=%s",
                    start,
                    len(rows),
                    loaded,
                )
                next_start = data.get("next")
                if next_start is None:
                    logger.info("sync_products: reached final page at start=%s", start)
                    break
                start = next_start

        added = 0
        updated = 0
        for service_id, value in synced.items():
            old_value = existing.get(service_id)
            if old_value is None:
                added += 1
            elif old_value != value:
                updated += 1

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(synced, f, ensure_ascii=False, indent=2)

        logger.info(
            "sync_products: completed loaded=%s added=%s updated=%s categories=%s saved_to=%s",
            loaded,
            added,
            updated,
            len({v.get("category_id") for v in synced.values()}),
            output_path,
        )
        return {
            "loaded": loaded,
            "added": added,
            "updated": updated,
            "total": len(synced),
            "path": str(output_path),
        }
 

_bitrix_singleton = BitrixService()


async def create_lab_item(
    client_data: dict,
    cart_items: list,
    total_sum: float,
    kp_number: str | None = None,
) -> int | None:
    return await _bitrix_singleton.create_lab_item(client_data, cart_items, total_sum, kp_number)


async def get_current_stage(item_id: int) -> str:
    return await _bitrix_singleton.get_current_stage(item_id)


async def find_or_create_company(
    session: aiohttp.ClientSession,
    base_url: str,
    inn: str,
    company_name: str,
    client_data: dict[str, Any] | None = None,
) -> int | None:
    return await _bitrix_singleton.find_or_create_company(session, base_url, inn, company_name, client_data)


async def find_or_create_contact(
    session: aiohttp.ClientSession,
    base_url: str,
    fio: str,
    phone: str,
    email: str,
    force_create: bool = True,
) -> int | None:
    return await _bitrix_singleton.find_or_create_contact(
        session,
        base_url,
        fio,
        phone,
        email,
        force_create,
    )
