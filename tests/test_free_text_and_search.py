from __future__ import annotations

from bot.handlers.free_text import _is_domain_like_query
from bot.services.price_loader import PriceLoader


def test_domain_like_query_detection():
    assert _is_domain_like_query("Нужен анализ сточной воды")
    assert _is_domain_like_query("Сформировать КП по пробоотбору")
    assert not _is_domain_like_query("Как настроение?")


def test_strict_search_filters_generic_phrase():
    PriceLoader._instance = None
    loader = PriceLoader.get()

    generic = loader.search("как вы работаете", limit=3, strict=True)
    assert generic == []


def test_strict_search_keeps_domain_relevance():
    PriceLoader._instance = None
    loader = PriceLoader.get()

    results = loader.search("анализ воды на нефтепродукты", limit=5, strict=True)
    assert results
    assert any("вод" in svc.name.lower() or "вод" in svc.category_name.lower() for svc in results)
