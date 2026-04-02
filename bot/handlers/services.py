"""Handler: Catalog browsing by category, service selection, add to cart from catalog.

Flow:
  "📋 Каталог услуг" button -> categories list -> services in category (paginated)
  -> service detail -> add with quantity
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.database.session import get_session
from bot.keyboards.categories import (
    categories_keyboard,
    quantity_keyboard,
    service_detail_keyboard,
    services_keyboard,
)
from bot.services.cart_service import add_item
from bot.services.price_loader import PriceLoader

router = Router(name="services")
logger = logging.getLogger(__name__)


@router.message(F.text == "📋 Каталог услуг")
async def handle_catalog_button(message: Message) -> None:
    """Show top-level categories."""
    await message.answer(
        "📋 <b>Каталог услуг Аналитик.Лаб</b>\n\nВыберите категорию:",
        reply_markup=categories_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "catalog")
async def callback_catalog(callback: CallbackQuery) -> None:
    await callback.message.answer(  # type: ignore[union-attr]
        "📋 <b>Каталог услуг</b>\n\nВыберите категорию:",
        reply_markup=categories_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def callback_category(callback: CallbackQuery) -> None:
    """Show services in a specific category."""
    category_id = callback.data.split(":")[1]  # type: ignore[union-attr]
    loader = PriceLoader.get()
    cat = loader.get_category(category_id)
    if not cat:
        await callback.answer("Категория не найдена", show_alert=True)
        return

    kb = services_keyboard(category_id, page=0)
    if kb:
        await callback.message.answer(  # type: ignore[union-attr]
            f"📂 <b>{cat.name}</b>\n\nВыберите услугу:",
            reply_markup=kb,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("catpg:"))
async def callback_category_page(callback: CallbackQuery) -> None:
    """Paginate within a category."""
    parts = callback.data.split(":")  # type: ignore[union-attr]
    category_id = parts[1]
    page = int(parts[2])

    kb = services_keyboard(category_id, page=page)
    if kb and callback.message:
        await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("svc:"))
async def callback_service_detail(callback: CallbackQuery) -> None:
    """Show individual service detail with add buttons."""
    service_id = callback.data.split(":")[1]  # type: ignore[union-attr]
    loader = PriceLoader.get()
    svc = loader.get_service(service_id)
    if not svc:
        await callback.answer("Услуга не найдена", show_alert=True)
        return

    text = (
        f"🔬 <b>{svc.name}</b>\n\n"
        f"💰 Цена: {svc.price:,.2f} руб. (без НДС)\n"
        f"📏 Единица: {svc.unit}\n"
        f"📂 Категория: {svc.category_name}\n\n"
        "Выберите количество:"
    )
    await callback.message.answer(  # type: ignore[union-attr]
        text, reply_markup=service_detail_keyboard(service_id), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("addqty:"))
async def callback_add_qty(callback: CallbackQuery) -> None:
    """Show quantity selection keyboard."""
    service_id = callback.data.split(":")[1]  # type: ignore[union-attr]
    await callback.message.answer(  # type: ignore[union-attr]
        "Выберите количество:", reply_markup=quantity_keyboard(service_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add:"))
async def callback_add_to_cart(callback: CallbackQuery) -> None:
    """Add service to cart (from catalog or low-confidence suggestions)."""
    parts = callback.data.split(":")  # type: ignore[union-attr]
    service_id = parts[1]
    quantity = int(parts[2]) if len(parts) > 2 else 1

    user_id = callback.from_user.id
    loader = PriceLoader.get()
    svc = loader.get_service(service_id)
    if not svc:
        await callback.answer("Услуга не найдена", show_alert=True)
        return

    try:
        async with get_session() as session:
            item = await add_item(session, user_id, service_id, quantity)
            if item:
                await callback.message.answer(  # type: ignore[union-attr]
                    f"✅ Добавлено в корзину:\n"
                    f"<b>{svc.name}</b> × {quantity}\n"
                    f"Цена: {svc.price:,.2f} руб./{svc.unit}",
                    parse_mode="HTML",
                )
            else:
                await callback.message.answer(  # type: ignore[union-attr]
                    "❌ Не удалось добавить услугу."
                )
    except Exception:
        logger.exception("Error adding to cart")
        await callback.message.answer(  # type: ignore[union-attr]
            "❌ Произошла ошибка при добавлении в корзину."
        )
    await callback.answer()


@router.callback_query(F.data == "back_cat")
async def callback_back_to_categories(callback: CallbackQuery) -> None:
    await callback.message.answer(  # type: ignore[union-attr]
        "📋 Выберите категорию:",
        reply_markup=categories_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()
