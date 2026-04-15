"""Load, cache and search the price list (preyskurant_2026.json).

Provides:
- Singleton loader with in-memory cache
- Search by service name (fuzzy via substring + normalisation)
- Lookup by service_id
- Category listing
- Full service list for LLM prompt injection
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from bot.config import DATA_DIR

logger = logging.getLogger(__name__)

_PRICE_FILE = DATA_DIR / "preyskurant_2026.json"


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    unit: str
    price: float
    nds_5pct: float
    category_id: str
    category_name: str
    is_percentage: bool = False
    percentage_rate: float = 0.0


@dataclass
class Category:
    id: str
    name: str
    services: list[Service] = field(default_factory=list)


class PriceLoader:
    _instance: Optional[PriceLoader] = None

    def __init__(self) -> None:
        self._categories: list[Category] = []
        self._services_by_id: dict[str, Service] = {}
        self._all_services: list[Service] = []

    @classmethod
    def get(cls) -> PriceLoader:
        if cls._instance is None:
            cls._instance = PriceLoader()
            cls._instance.load()
        return cls._instance

    def load(self, path: Path | None = None) -> None:
        fpath = path or _PRICE_FILE
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)

        self._categories = []
        self._services_by_id = {}
        self._all_services = []

        for cat_data in data.get("categories", []):
            cat = Category(id=cat_data["id"], name=cat_data["name"])
            for svc_data in cat_data.get("services", []):
                svc = Service(
                    id=svc_data["id"],
                    name=svc_data["name"],
                    unit=svc_data.get("unit", "шт"),
                    price=float(svc_data.get("price", 0)),
                    nds_5pct=float(svc_data.get("nds_5pct", 0)),
                    category_id=cat.id,
                    category_name=cat.name,
                    is_percentage=svc_data.get("is_percentage", False),
                    percentage_rate=float(svc_data.get("percentage_rate", 0)),
                )
                cat.services.append(svc)
                self._services_by_id[svc.id] = svc
                self._all_services.append(svc)
            self._categories.append(cat)

        logger.info(
            "Price list loaded: %d categories, %d services",
            len(self._categories),
            len(self._all_services),
        )

    @property
    def categories(self) -> list[Category]:
        return self._categories

    def get_category(self, category_id: str) -> Category | None:
        for cat in self._categories:
            if cat.id == category_id:
                return cat
        return None

    def get_service(self, service_id: str) -> Service | None:
        return self._services_by_id.get(service_id)

    def validate_service_ids(self, service_ids: list[str]) -> list[str]:
        """Return list of invalid service_ids."""
        return [sid for sid in service_ids if sid not in self._services_by_id]

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {
            token for token in re.split(r"[^a-zA-Zа-яА-Я0-9]+", text.lower())
            if len(token) >= 3
        }

    def search(self, query: str, limit: int = 10, strict: bool = False) -> list[Service]:
        """Search services by name using substring, tokens and fuzzy ratio."""
        q = query.lower().strip()
        if not q:
            return []

        query_tokens = self._tokenize(q)
        scored: list[tuple[float, Service]] = []
        for svc in self._all_services:
            name_lower = svc.name.lower()
            name_tokens = self._tokenize(name_lower)
            overlap = len(query_tokens & name_tokens) if query_tokens else 0

            if q in name_lower:
                score = 0.9 + len(q) / len(name_lower) * 0.1
                score += min(overlap * 0.03, 0.09)
                scored.append((score, svc))
            else:
                ratio = SequenceMatcher(None, q, name_lower).ratio()
                threshold = 0.55 if strict else 0.42
                if ratio < threshold and overlap == 0:
                    continue
                if strict and len(query_tokens) >= 2 and overlap == 0:
                    continue
                score = ratio + min(overlap * 0.08, 0.24)
                scored.append((score, svc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def services_summary_for_llm(self) -> str:
        """Compact text with all services for LLM system prompt."""
        lines: list[str] = []
        for cat in self._categories:
            lines.append(f"\n## {cat.name}")
            for svc in cat.services:
                if svc.is_percentage:
                    lines.append(f"- [{svc.id}] {svc.name} ({svc.unit})")
                else:
                    lines.append(
                        f"- [{svc.id}] {svc.name} — {svc.price:.0f} руб./{svc.unit}"
                    )
        return "\n".join(lines)
