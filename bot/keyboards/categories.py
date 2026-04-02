"""Inline keyboards for browsing service categories and selecting services."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.price_loader import Category, PriceLoader, Service


def categories_keyboard() -> InlineKeyboardMarkup:
    """Top-level category selection keyboard."""
    loader = PriceLoader.get()
    buttons = []
    for cat in loader.categories:
        buttons.append(
            [InlineKeyboardButton(text=cat.name, callback_data=f"cat:{cat.id}")]
        )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_menu")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def services_keyboard(category_id: str, page: int = 0, page_size: int = 8) -> InlineKeyboardMarkup | None:
    """Paginated service list within a category."""
    loader = PriceLoader.get()
    cat = loader.get_category(category_id)
    if not cat:
        return None

    # Filter out percentage-based services from the browsable catalog
    services = [s for s in cat.services if not s.is_percentage]
    total = len(services)
    start = page * page_size
    end = min(start + page_size, total)
    page_services = services[start:end]

    buttons = []
    for svc in page_services:
        label = f"{svc.name[:45]} — {svc.price:,.0f}₽"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"svc:{svc.id}")]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"catpg:{category_id}:{page - 1}")
        )
    if end < total:
        nav_row.append(
            InlineKeyboardButton(text="➡️", callback_data=f"catpg:{category_id}:{page + 1}")
        )
    if nav_row:
        buttons.append(nav_row)

    buttons.append(
        [InlineKeyboardButton(text="📋 Все категории", callback_data="catalog")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def service_detail_keyboard(service_id: str) -> InlineKeyboardMarkup:
    """Keyboard shown after selecting a specific service."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить 1 шт.", callback_data=f"add:{service_id}:1"),
                InlineKeyboardButton(text="➕ 3 шт.", callback_data=f"add:{service_id}:3"),
            ],
            [
                InlineKeyboardButton(text="➕ Указать кол-во", callback_data=f"addqty:{service_id}"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к категории", callback_data="back_cat"),
            ],
        ]
    )


def quantity_keyboard(service_id: str) -> InlineKeyboardMarkup:
    """Quick-select quantity buttons."""
    buttons = []
    row: list[InlineKeyboardButton] = []
    for q in [1, 2, 3, 5, 10]:
        row.append(
            InlineKeyboardButton(text=str(q), callback_data=f"add:{service_id}:{q}")
        )
    buttons.append(row)
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_cat")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
