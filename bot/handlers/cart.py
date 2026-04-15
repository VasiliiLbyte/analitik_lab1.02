"""Handler: Cart viewing, removing items, clearing, repeat last order.

Protection scenarios:
- Empty cart + attempt to create KP -> "Корзина пуста"
- Remove all items -> correct empty state handling
- Duplicate add (handled in cart_service via upsert)
- Repeat last order when no previous orders exist
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.session import get_session
from bot.keyboards.cart import cart_keyboard, empty_cart_keyboard
from bot.keyboards.main import back_cancel_keyboard
from bot.services.cart_service import (
    clear_cart,
    format_cart_text,
    get_cart_summary,
    remove_item,
    repeat_last_order,
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
            "<b>Шаг 1/9:</b> Введите ИНН (10 или 12 цифр):",
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
            "<b>Шаг 1/9:</b> Введите ИНН (10 или 12 цифр):",
            parse_mode="HTML",
            reply_markup=back_cancel_keyboard(),
        )
    except Exception:
        logger.exception("Error starting KP form")
        await message.answer("❌ Ошибка при запуске формы КП.")


@router.message(F.text == "🔄 Повторить заказ")
async def handle_repeat_order(message: Message) -> None:
    """Repeat last order: copy previous order items into the cart."""
    user_id = message.from_user.id  # type: ignore[union-attr]
    try:
        async with get_session() as session:
            items = await repeat_last_order(session, user_id)

        if items:
            names = "\n".join(f"• {it.service_name} ×{it.quantity}" for it in items)
            await message.answer(
                f"🔄 Заказ повторён! В корзину добавлено:\n{names}",
                parse_mode="HTML",
            )
        else:
            await message.answer("У вас пока нет предыдущих заказов для повторения.")
    except Exception:
        logger.exception("Error repeating order")
        await message.answer("❌ Ошибка при повторении заказа.")
