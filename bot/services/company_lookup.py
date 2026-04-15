"""DaData company lookup by INN for KP form autofill."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from bot.config import get_settings

logger = logging.getLogger(__name__)

_DADATA_FIND_BY_INN_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


@dataclass(frozen=True)
class CompanyInfo:
    name: str
    kpp: str
    address: str
    ogrn: str = ""
    status: str = ""


def _is_lookup_enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.dadata_enabled
        and settings.dadata_api_key.strip()
    )


async def lookup_company_by_inn(inn: str) -> CompanyInfo | None:
    """Lookup a company by INN in DaData. Returns None on any failure."""
    settings = get_settings()
    if not _is_lookup_enabled():
        return None

    headers = {
        "Authorization": f"Token {settings.dadata_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if settings.dadata_secret_key.strip():
        headers["X-Secret"] = settings.dadata_secret_key

    payload = {"query": inn, "count": 1}
    timeout = aiohttp.ClientTimeout(total=settings.dadata_timeout_sec)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _DADATA_FIND_BY_INN_URL,
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status >= 400:
                    logger.warning("DaData lookup HTTP %s for INN", resp.status)
                    return None
                body = await resp.json()
    except Exception:
        logger.exception("DaData lookup failed")
        return None

    suggestions = body.get("suggestions") or []
    if not suggestions:
        return None

    data = (suggestions[0] or {}).get("data") or {}
    name = (
        data.get("name", {}).get("short_with_opf")
        or data.get("name", {}).get("full_with_opf")
        or (suggestions[0] or {}).get("value")
        or ""
    ).strip()
    kpp = str(data.get("kpp") or "").strip()
    address = (
        data.get("address", {}).get("unrestricted_value")
        or data.get("address", {}).get("value")
        or ""
    ).strip()
    ogrn = str(data.get("ogrn") or "").strip()
    status = str((data.get("state") or {}).get("status") or "").strip()

    if not name:
        return None

    return CompanyInfo(
        name=name,
        kpp=kpp,
        address=address,
        ogrn=ogrn,
        status=status,
    )
