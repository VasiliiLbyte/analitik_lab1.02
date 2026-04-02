"""Tests for bot.services.kp_generator — data building, amount in words, Word generation."""

from __future__ import annotations

import datetime
import os
from types import SimpleNamespace

import pytest

from bot.services.kp_generator import (
    KPData,
    _amount_in_words,
    _decline_kopecks,
    _decline_rubles,
    _fmt,
    _format_date_ru,
    build_kp_data,
    generate_kp,
)


class TestFormatHelpers:
    def test_date_formatting(self):
        d = datetime.date(2026, 3, 26)
        assert _format_date_ru(d) == "26 марта 2026 г."

    def test_fmt_thousands_separator(self):
        assert _fmt(37500.00) == "37 500.00"
        assert _fmt(0.50) == "0.50"

    def test_decline_rubles(self):
        assert _decline_rubles(1) == "рубль"
        assert _decline_rubles(2) == "рубля"
        assert _decline_rubles(5) == "рублей"
        assert _decline_rubles(11) == "рублей"
        assert _decline_rubles(21) == "рубль"
        assert _decline_rubles(22) == "рубля"

    def test_decline_kopecks(self):
        assert _decline_kopecks(1) == "копейка"
        assert _decline_kopecks(3) == "копейки"
        assert _decline_kopecks(5) == "копеек"
        assert _decline_kopecks(11) == "копеек"

    def test_amount_in_words(self):
        text = _amount_in_words(37500.00)
        assert "тридцать семь тысяч пятьсот" in text.lower()
        assert "рублей" in text
        assert "00 копеек" in text


def _make_cart_items(count: int = 2):
    """Create fake cart items for testing build_kp_data."""
    items = []
    for i in range(count):
        items.append(SimpleNamespace(
            service_id=f"svc_{i}",
            service_name=f"Test Service {i+1}",
            category_name="Water Analysis" if i % 2 == 0 else "Soil Analysis",
            quantity=i + 1,
            unit="проба",
            unit_price=1000.0 * (i + 1),
        ))
    return items


class TestBuildKPData:
    def test_grouping_and_totals(self):
        cart = _make_cart_items(3)
        data = build_kp_data(
            kp_number="НФ-001",
            kp_date=datetime.date(2026, 4, 1),
            customer_name="OOO Test",
            customer_inn="7806341520",
            customer_kpp="781601001",
            customer_address="Test Address",
            contact_person="Иванов",
            contact_info="+7 999 123-45-67",
            cart_items=cart,
        )

        assert data.kp_number == "НФ-001"
        assert len(data.groups) == 2
        assert data.subtotal > 0
        assert data.protocol_fee == round(data.subtotal * 3.0 / 100, 2)
        assert data.nds > 0
        assert data.total > data.subtotal
        assert "рубл" in data.total_words.lower()
        assert data.total_items == 1 + 2 + 3

    def test_empty_cart(self):
        data = build_kp_data(
            kp_number="НФ-002",
            kp_date=datetime.date(2026, 1, 1),
            customer_name="X", customer_inn="", customer_kpp="",
            customer_address="", contact_person="", contact_info="",
            cart_items=[],
        )
        assert data.subtotal == 0.0
        assert data.total == 0.0
        assert len(data.groups) == 0


class TestGenerateKP:
    def test_generates_docx_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot.services.kp_generator.GENERATED_DIR", tmp_path)

        data = build_kp_data(
            kp_number="НФ-TEST",
            kp_date=datetime.date(2026, 4, 2),
            customer_name='ООО "Тест"',
            customer_inn="7806341520",
            customer_kpp="781601001",
            customer_address="г. СПб, ул. Тестовая, д. 1",
            contact_person="Петров П.П.",
            contact_info="test@example.com",
            cart_items=_make_cart_items(2),
        )
        path = generate_kp(data)

        assert path.exists()
        assert path.suffix == ".docx"
        assert os.path.getsize(path) > 0
