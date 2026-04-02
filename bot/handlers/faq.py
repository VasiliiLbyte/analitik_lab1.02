"""Handler: FAQ and lab information.

Responds to the FAQ and "О лаборатории" menu buttons,
and handles explain queries from the LLM router.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.keyboards.main import main_menu_keyboard

router = Router(name="faq")
logger = logging.getLogger(__name__)

FAQ_TEXT = (
    "❓ <b>Часто задаваемые вопросы</b>\n\n"
    "<b>Какие услуги вы предоставляете?</b>\n"
    "Мы выполняем анализ воды (питьевой, сточной, природной), почвы, грунтов, "
    "воздуха, а также проводим измерения физических факторов (шум, вибрация, ЭМИ).\n\n"
    "<b>Какие сроки выполнения?</b>\n"
    "Стандартные сроки — 5-10 рабочих дней. Срочные анализы — от 1 рабочего дня "
    "(стоимость ×1.4-1.5).\n\n"
    "<b>Вы выезжаете на объект?</b>\n"
    "Да! Выезд инженера по Санкт-Петербургу — 4 830 руб., "
    "по Ленинградской области — 7 940 руб.\n\n"
    "<b>Как формируется цена?</b>\n"
    "К стоимости анализов добавляется 3% за оформление протоколов "
    "и 5% НДС.\n\n"
    "<b>Какие документы я получу?</b>\n"
    "Протокол испытаний, аккредитованный в системе ФСА. "
    "Скан-копия — 170 руб., загрузка в ФСА — 90 руб.\n\n"
    "<b>Есть ли скидки?</b>\n"
    "При заказе от 21 000 руб. (без НДС) по воздуху и от 30 450 руб. "
    "по грунтам — цены не увеличиваются. Индивидуальные скидки обсуждаются."
)

ABOUT_TEXT = (
    "🏢 <b>ООО «АНАЛИТИК.ЛАБ»</b>\n\n"
    "Экологическая лаборатория в Санкт-Петербурге.\n\n"
    "📍 <b>Адрес:</b>\n"
    "192102, Санкт-Петербург, ул. Дубровская, д. 13, лит. А, пом. 2-Н, к. 27\n\n"
    "🔢 <b>ИНН:</b> 7806341520\n"
    "🔢 <b>КПП:</b> 781601001\n\n"
    "🌐 <b>Сайт:</b> analitik-lab.ru\n\n"
    "<b>Направления работы:</b>\n"
    "• Анализ воды (питьевая, сточная, природная, морская)\n"
    "• Анализ почв, грунтов, донных отложений\n"
    "• Анализ воздуха (промышленные выбросы, атмосферный)\n"
    "• Физические факторы (шум, вибрация, ЭМИ, освещение)\n"
    "• Оформление протоколов и документации\n"
    "• Выезд инженера и отбор проб"
)


@router.message(F.text == "❓ FAQ")
async def handle_faq(message: Message) -> None:
    await message.answer(FAQ_TEXT, parse_mode="HTML")


@router.message(F.text == "ℹ️ О лаборатории")
async def handle_about(message: Message) -> None:
    await message.answer(ABOUT_TEXT, parse_mode="HTML")


@router.callback_query(F.data == "back_menu")
async def callback_back_menu(callback: CallbackQuery) -> None:
    await callback.message.answer(  # type: ignore[union-attr]
        "Главное меню:", reply_markup=main_menu_keyboard(), parse_mode="HTML"
    )
    await callback.answer()
