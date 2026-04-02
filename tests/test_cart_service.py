"""Tests for bot.services.cart_service — add, dedup, remove, clear, summary with 3% fee."""

from __future__ import annotations

import pytest

from bot.services.cart_service import (
    add_item,
    clear_cart,
    get_cart_items,
    get_cart_summary,
    get_or_create_user,
    remove_item,
)
from bot.services.price_loader import PriceLoader


@pytest.fixture(autouse=True)
def _load_prices():
    """Ensure the price list is loaded before each test."""
    PriceLoader._instance = None
    PriceLoader.get()


TELEGRAM_ID = 111222333


@pytest.mark.asyncio
async def test_get_or_create_user(db_session):
    user1 = await get_or_create_user(db_session, TELEGRAM_ID, username="test")
    user2 = await get_or_create_user(db_session, TELEGRAM_ID)
    assert user1.id == user2.id
    assert user1.telegram_id == TELEGRAM_ID


@pytest.mark.asyncio
async def test_add_item_and_deduplication(db_session):
    loader = PriceLoader.get()
    first_service = loader._all_services[0]

    item = await add_item(db_session, TELEGRAM_ID, first_service.id, quantity=2)
    assert item is not None
    assert item.quantity == 2

    item2 = await add_item(db_session, TELEGRAM_ID, first_service.id, quantity=3)
    assert item2 is not None
    assert item2.quantity == 5


@pytest.mark.asyncio
async def test_add_unknown_service(db_session):
    result = await add_item(db_session, TELEGRAM_ID, "NONEXISTENT_ID")
    assert result is None


@pytest.mark.asyncio
async def test_remove_item(db_session):
    loader = PriceLoader.get()
    svc = loader._all_services[0]

    await add_item(db_session, TELEGRAM_ID, svc.id)
    removed = await remove_item(db_session, TELEGRAM_ID, svc.id)
    assert removed is True

    removed_again = await remove_item(db_session, TELEGRAM_ID, svc.id)
    assert removed_again is False


@pytest.mark.asyncio
async def test_clear_cart(db_session):
    loader = PriceLoader.get()
    for svc in loader._all_services[:3]:
        await add_item(db_session, TELEGRAM_ID, svc.id)

    count = await clear_cart(db_session, TELEGRAM_ID)
    assert count == 3

    items = await get_cart_items(db_session, TELEGRAM_ID)
    assert len(items) == 0


@pytest.mark.asyncio
async def test_cart_summary_includes_protocol_fee_and_nds(db_session):
    loader = PriceLoader.get()
    svc = loader._all_services[0]

    await add_item(db_session, TELEGRAM_ID, svc.id, quantity=1)
    summary = await get_cart_summary(db_session, TELEGRAM_ID)

    assert summary.item_count == 1
    assert summary.subtotal == svc.price
    assert summary.protocol_fee == round(svc.price * 3.0 / 100, 2)
    assert summary.nds > 0
    assert summary.total > summary.subtotal


@pytest.mark.asyncio
async def test_empty_cart_summary(db_session):
    summary = await get_cart_summary(db_session, TELEGRAM_ID)
    assert summary.item_count == 0
    assert summary.total == 0.0
