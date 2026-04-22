"""Handler: Cart viewing, removing items, clearing, repeat last order.

Protection scenarios:
- Empty cart + attempt to create KP -> "Корзина пуста"
- Remove all items -> correct empty state handling
- Duplicate add (handled in cart_service via upsert)
- Repeat last order when no previous orders exist
"""

from __future__ import annotations

import logging
import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.database.models import Order, User
from bot.database.session import get_session
from bot.keyboards.cart import cart_keyboard, empty_cart_keyboard
from bot.keyboards.main import back_cancel_keyboard
from bot.services import bitrix_service
from bot.services.cart_service import (
    clear_cart,
    format_cart_text,
    get_cart_summary,
    remove_item,
)
from bot.states.kp_form import KPForm

router = Router(name="cart")
logger = logging.getLogger(__name__)


@router.message(F.text == "🛒 Корзина")
async def handle_cart_button(message: Message) -> None:
    """Show current cart contents."""
    try:
        async with get_session() as session:
            summary = await get_cart_summary(session, message.from_user.id)  # type: ignore[union-attr]

        text = format_cart_text(summary)
        kb = cart_keyboard(summary.items) if summary.items else empty_cart_keyboard()
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        logger.exception("Error showing cart")
        await message.answer("❌ Ошибка при загрузке корзины.")


@router.callback_query(F.data.startswith("rm:"))
async def callback_remove_item(callback: CallbackQuery) -> None:
    """Remove a specific item from cart."""
    service_id = callback.data.split(":")[1]  # type: ignore[union-attr]
    user_id = callback.from_user.id

    try:
        async with get_session() as session:
            removed = await remove_item(session, user_id, service_id)

        if removed:
            async with get_session() as session:
                summary = await get_cart_summary(session, user_id)
            text = format_cart_text(summary)
            kb = cart_keyboard(summary.items) if summary.items else empty_cart_keyboard()
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")  # type: ignore[union-attr]
        else:
            await callback.answer("Позиция не найдена", show_alert=True)
    except Exception:
        logger.exception("Error removing item from cart")
        await callback.answer("Ошибка удаления", show_alert=True)
    await callback.answer()


@router.callback_query(F.data == "clear_cart")
async def callback_clear_cart(callback: CallbackQuery) -> None:
    """Clear the entire cart."""
    user_id = callback.from_user.id
    try:
        async with get_session() as session:
            count = await clear_cart(session, user_id)
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"🗑 Корзина очищена (удалено позиций: {count}).",
            reply_markup=empty_cart_keyboard(),
        )
    except Exception:
        logger.exception("Error clearing cart")
        await callback.answer("Ошибка очистки", show_alert=True)
    await callback.answer()


@router.callback_query(F.data == "create_kp")
async def callback_create_kp(callback: CallbackQuery, state: FSMContext) -> None:
    """Start KP form — only if cart is not empty."""
    user_id = callback.from_user.id
    try:
        async with get_session() as session:
            summary = await get_cart_summary(session, user_id)

        if not summary.items:
            await callback.message.answer(  # type: ignore[union-attr]
                "🛒 Корзина пуста. Сначала добавьте услуги.",
                reply_markup=empty_cart_keyboard(),
            )
            await callback.answer()
            return

        await state.set_state(KPForm.inn)
        await callback.message.answer(  # type: ignore[union-attr]
            "📄 <b>Формирование коммерческого предложения</b>\n\n"
            f"В корзине: {summary.item_count} позиций на сумму "
            f"{summary.total:,.2f} руб. (с НДС)\n\n"
            "<b>Шаг 1/7:</b> Введите ИНН (10 или 12 цифр):",
            parse_mode="HTML",
            reply_markup=back_cancel_keyboard(),
        )
    except Exception:
        logger.exception("Error starting KP form")
        await callback.answer("Ошибка", show_alert=True)
    await callback.answer()


@router.message(F.text == "📄 Сформировать КП")
async def handle_create_kp_button(message: Message, state: FSMContext) -> None:
    """Start KP form from main menu button."""
    user_id = message.from_user.id  # type: ignore[union-attr]
    try:
        async with get_session() as session:
            summary = await get_cart_summary(session, user_id)

        if not summary.items:
            await message.answer(
                "🛒 Корзина пуста. Сначала добавьте услуги.",
                reply_markup=empty_cart_keyboard(),
            )
            return

        await state.set_state(KPForm.inn)
        await message.answer(
            "📄 <b>Формирование коммерческого предложения</b>\n\n"
            f"В корзине: {summary.item_count} позиций на сумму "
            f"{summary.total:,.2f} руб. (с НДС)\n\n"
            "<b>Шаг 1/7:</b> Введите ИНН (10 или 12 цифр):",
            parse_mode="HTML",
            reply_markup=back_cancel_keyboard(),
        )
    except Exception:
        logger.exception("Error starting KP form")
        await message.answer("❌ Ошибка при запуске формы КП.")


@router.message(F.text == "📦 Статус заказа")
async def handle_order_status(message: Message) -> None:
    """Show current status for up to 5 latest Bitrix-linked orders."""
    telegram_id = message.from_user.id  # type: ignore[union-attr]
    try:
        async with get_session() as session:
            user_result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = user_result.scalar_one_or_none()
            if user is None:
                await message.answer("У вас пока нет заказов, синхронизированных с Bitrix24.")
                return

            result = await session.execute(
                select(Order)
                .where(
                    Order.user_id == user.id,
                    Order.bitrix_item_id.is_not(None),
                )
                .order_by(Order.created_at.desc())
                .limit(5)
            )
            orders = list(result.scalars().all())

        if not orders:
            await message.answer("У вас пока нет заказов, синхронизированных с Bitrix24.")
            return

        async def _get_stage(order: Order) -> tuple[Order, str]:
            try:
                stage_name = await bitrix_service.get_current_stage(order.bitrix_item_id)
                return order, stage_name
            except Exception:
                logger.exception("Error getting stage for order_id=%s", order.id)
                return order, "unknown"

        staged_orders = await asyncio.gather(*[_get_stage(order) for order in orders])
        visible_orders = [
            (order, stage_name)
            for order, stage_name in staged_orders
            if str(stage_name).strip().lower() != "unknown"
        ]
        if not visible_orders:
            await message.answer("У вас пока нет активных заказов с определённым статусом.")
            return

        if len(visible_orders) == 1:
            order, stage_name = visible_orders[0]
            await message.answer(f"Ваш заказ №{order.id} сейчас в статусе: {stage_name}")
            return

        lines = [f"• №{order.id} — {stage_name}" for order, stage_name in visible_orders]
        await message.answer("Ваши последние заказы:\n" + "\n".join(lines))
    except Exception:
        logger.exception("Error checking order status")
        await message.answer("❌ Не удалось получить статус заказа. Попробуйте позже.")


async def handle_repeat_order(message: Message) -> None:
    """Backward-compatible alias used by free-text intent routing."""
    await handle_order_status(message)
