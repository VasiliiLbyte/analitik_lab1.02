"""Main menu reply keyboard and common inline keyboards."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📋 Каталог услуг"),
                KeyboardButton(text="🛒 Корзина"),
            ],
            [
                KeyboardButton(text="📄 Сформировать КП"),
                KeyboardButton(text="📦 Статус заказа"),
            ],
            [
                KeyboardButton(text="❓ FAQ"),
                KeyboardButton(text="ℹ️ О лаборатории"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напишите, что вас интересует...",
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ]
    )


def back_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="kp_back"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="kp_cancel"),
            ]
        ]
    )


def confirm_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="kp_confirm"),
                InlineKeyboardButton(text="✏️ Редактировать", callback_data="kp_edit"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="kp_cancel")],
        ]
    )


def sample_return_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data="sample_return:yes"),
                InlineKeyboardButton(text="❌ Нет", callback_data="sample_return:no"),
                InlineKeyboardButton(text="📝 Уточню", callback_data="sample_return:later"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="kp_back"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="kp_cancel"),
            ],
        ]
    )


def low_confidence_keyboard(suggestions: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Keyboard with top-N service suggestions when LLM confidence is low."""
    buttons = [
        [InlineKeyboardButton(text=name[:60], callback_data=f"add:{sid}")]
        for sid, name in suggestions
    ]
    buttons.append(
        [InlineKeyboardButton(text="📋 Открыть каталог", callback_data="catalog")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def inn_autofill_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить и заполнить", callback_data="inn_autofill:accept")],
            [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="inn_autofill:manual")],
        ]
    )
