"""Cart business logic: add/remove/clear, deduplication, 3% protocol fee, repeat order.

The 3% "Оформление протоколов" fee is calculated dynamically — not stored as a line item
in the cart, but computed when displaying the cart and when generating KP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import CartItem, Order, OrderItem, User
from bot.services.price_loader import PriceLoader

logger = logging.getLogger(__name__)


@dataclass
class CartSummary:
    items: list[CartItem]
    subtotal: float
    protocol_fee: float
    total_before_nds: float
    nds: float
    total: float
    item_count: int


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, username: str | None = None, first_name: str | None = None
) -> User:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name)
        session.add(user)
        await session.flush()
    return user


async def add_item(
    session: AsyncSession,
    telegram_id: int,
    service_id: str,
    quantity: int = 1,
) -> CartItem | None:
    """Add a service to cart. If already present, increase quantity (dedup)."""
    loader = PriceLoader.get()
    svc = loader.get_service(service_id)
    if svc is None:
        logger.warning("Attempt to add unknown service: %s", service_id)
        return None

    user = await get_or_create_user(session, telegram_id)

    result = await session.execute(
        select(CartItem).where(
            CartItem.user_id == user.id, CartItem.service_id == service_id
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.quantity += quantity
        await session.flush()
        return existing

    item = CartItem(
        user_id=user.id,
        service_id=svc.id,
        service_name=svc.name,
        category_name=svc.category_name,
        quantity=quantity,
        unit=svc.unit,
        unit_price=svc.price,
    )
    session.add(item)
    await session.flush()
    return item


async def remove_item(
    session: AsyncSession, telegram_id: int, service_id: str
) -> bool:
    user = await get_or_create_user(session, telegram_id)
    result = await session.execute(
        delete(CartItem).where(
            CartItem.user_id == user.id, CartItem.service_id == service_id
        )
    )
    return result.rowcount > 0  # type: ignore[union-attr]


async def clear_cart(session: AsyncSession, telegram_id: int) -> int:
    user = await get_or_create_user(session, telegram_id)
    result = await session.execute(
        delete(CartItem).where(CartItem.user_id == user.id)
    )
    return result.rowcount  # type: ignore[union-attr]


async def get_cart_items(session: AsyncSession, telegram_id: int) -> list[CartItem]:
    user = await get_or_create_user(session, telegram_id)
    result = await session.execute(
        select(CartItem).where(CartItem.user_id == user.id).order_by(CartItem.added_at)
    )
    return list(result.scalars().all())


async def get_cart_summary(session: AsyncSession, telegram_id: int) -> CartSummary:
    """Compute full cart summary including dynamic 3% protocol fee and NDS."""
    settings = get_settings()
    items = await get_cart_items(session, telegram_id)

    subtotal = sum(item.unit_price * item.quantity for item in items)
    protocol_fee = round(subtotal * settings.protocol_fee_rate / 100, 2)
    total_before_nds = subtotal + protocol_fee
    nds = round(total_before_nds * settings.nds_rate / 100, 2)
    total = round(total_before_nds + nds, 2)

    return CartSummary(
        items=items,
        subtotal=round(subtotal, 2),
        protocol_fee=protocol_fee,
        total_before_nds=round(total_before_nds, 2),
        nds=nds,
        total=total,
        item_count=len(items),
    )


def format_cart_text(summary: CartSummary) -> str:
    """Human-readable cart text for Telegram message."""
    if not summary.items:
        return "🛒 Корзина пуста."

    lines: list[str] = ["🛒 <b>Ваша корзина:</b>\n"]
    for i, item in enumerate(summary.items, 1):
        line_total = item.unit_price * item.quantity
        lines.append(
            f"{i}. {item.service_name}\n"
            f"   {item.quantity} {item.unit} × {item.unit_price:,.2f} = "
            f"<b>{line_total:,.2f}</b> руб."
        )

    lines.append(f"\n📊 Сметная стоимость: {summary.subtotal:,.2f} руб.")
    lines.append(
        f"📋 Оформление протоколов (3%): {summary.protocol_fee:,.2f} руб."
    )
    lines.append(f"💰 Итого без НДС: {summary.total_before_nds:,.2f} руб.")
    lines.append(f"📌 НДС (5%): {summary.nds:,.2f} руб.")
    lines.append(f"<b>💵 ИТОГО с НДС: {summary.total:,.2f} руб.</b>")
    return "\n".join(lines)


def format_cart_for_llm(items: list[CartItem]) -> str:
    """Compact cart representation for the LLM system prompt."""
    if not items:
        return "Пуста"
    lines = []
    for item in items:
        lines.append(f"- {item.service_name} ({item.service_id}) x{item.quantity}")
    return "\n".join(lines)


async def repeat_last_order(
    session: AsyncSession, telegram_id: int
) -> Optional[list[CartItem]]:
    """Copy items from last completed order back into cart."""
    user = await get_or_create_user(session, telegram_id)

    result = await session.execute(
        select(Order)
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(1)
    )
    last_order = result.scalar_one_or_none()
    if last_order is None:
        return None

    result = await session.execute(
        select(OrderItem).where(OrderItem.order_id == last_order.id)
    )
    order_items = list(result.scalars().all())
    if not order_items:
        return None

    await clear_cart(session, telegram_id)
    added: list[CartItem] = []
    for oi in order_items:
        item = await add_item(session, telegram_id, oi.service_id, oi.quantity)
        if item:
            added.append(item)

    return added if added else None
