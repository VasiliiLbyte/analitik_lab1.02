"""Cart management inline keyboards."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import CartItem


def cart_keyboard(items: list[CartItem]) -> InlineKeyboardMarkup:
    """Cart view with remove buttons and action row."""
    buttons = []

    for item in items:
        label = f"❌ {item.service_name[:40]} (x{item.quantity})"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"rm:{item.service_id}")]
        )

    buttons.append([
        InlineKeyboardButton(text="📄 Сформировать КП", callback_data="create_kp"),
        InlineKeyboardButton(text="🗑 Очистить", callback_data="clear_cart"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📋 Каталог", callback_data="catalog"),
        InlineKeyboardButton(text="⬅️ Меню", callback_data="back_menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def empty_cart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Каталог услуг", callback_data="catalog"),
                InlineKeyboardButton(text="⬅️ Меню", callback_data="back_menu"),
            ]
        ]
    )


def cart_not_empty_warning_keyboard() -> InlineKeyboardMarkup:
    """Shown when user tries to start a new order with items in cart."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart"),
                InlineKeyboardButton(text="➕ Добавить к текущей", callback_data="catalog"),
            ],
        ]
    )
