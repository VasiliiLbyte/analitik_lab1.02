"""Handler: /start and /help commands.

Protection scenarios:
- /start at any moment (including mid-KP form) — clears FSM state
- Graceful reset of all user state
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.main import main_menu_keyboard

router = Router(name="start")
logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "🔬 <b>Добро пожаловать в Аналитик.Лаб!</b>\n\n"
    "Я помогу вам подобрать лабораторные услуги и сформировать "
    "коммерческое предложение.\n\n"
    "Вы можете:\n"
    "• Написать свободным текстом, что вам нужно\n"
    "• Использовать кнопки меню ниже\n"
    "• Просмотреть каталог услуг по категориям\n\n"
    "Для начала выберите действие или просто напишите, "
    "какие анализы вам необходимы."
)

HELP_TEXT = (
    "ℹ️ <b>Справка по боту Аналитик.Лаб</b>\n\n"
    "<b>Команды:</b>\n"
    "/start — Перезапустить бота\n"
    "/help — Показать справку\n\n"
    "<b>Как пользоваться:</b>\n"
    "1. Напишите, какие анализы нужны (например: «анализ воды на тяжёлые металлы»)\n"
    "2. Или откройте каталог и выберите услуги кнопками\n"
    "3. Проверьте корзину\n"
    "4. Заполните форму КП (6 шагов)\n"
    "5. Получите готовый документ Word\n\n"
    "Протоколы (3% от сметы) добавляются автоматически.\n"
    "НДС 5% рассчитывается при формировании КП."
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Handle /start — always clears FSM state and shows main menu."""
    await state.clear()
    logger.info(
        "User %d started bot (username=%s)",
        message.from_user.id,  # type: ignore[union-attr]
        message.from_user.username,  # type: ignore[union-attr]
    )
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
