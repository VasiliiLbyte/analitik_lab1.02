"""Handler: Free text catch-all — routes messages through LLM Intent Recognizer.

This handler is registered LAST so it only catches messages not handled by other routers.

Protection scenarios:
- Junk text (only digits/emoji/special) -> helpful fallback
- Messages > 2000 chars -> truncated before LLM
- Low confidence (< 0.75) -> show suggestion buttons
- LLM returns non-existing service -> filtered out
- LLM fails entirely -> fallback with category buttons
- Jailbreak attempts -> blocked at llm_intent level
"""

from __future__ import annotations

import logging
import re

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database.session import get_session
from bot.keyboards.categories import categories_keyboard
from bot.keyboards.main import low_confidence_keyboard, main_menu_keyboard
from bot.services.cart_service import (
    add_item,
    format_cart_for_llm,
    get_cart_items,
    set_item_quantity,
)
from bot.services.llm_intent import get_gigachat_client
from bot.services.price_loader import PriceLoader
from bot.states.kp_form import KPForm
from bot.utils.validators import is_junk_text, sanitise_for_llm

router = Router(name="free_text")
logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.75
_CART_NAV_KEYWORDS = {
    "корзина",
    "в корзину",
    "в корзине",
    "покажи корзину",
    "показать корзину",
    "перейди в корзину",
    "открой корзину",
    "открыть корзину",
    "cart",
    "🛒",
}
_CLEAR_CART_KEYWORDS = {
    "очисти корзину",
    "очистить корзину",
    "очисти карзину",
    "очистить карзину",
    "отчисти корзину",
    "отчистить корзину",
    "отчисти карзину",
    "отчистить карзину",
    "clear cart",
    "clear the cart",
}


@router.message(StateFilter(None))
async def handle_free_text(message: Message, state: FSMContext) -> None:
    """Catch-all handler for free-text messages — processes via LLM."""
    text = (message.text or "").strip()
    if not text:
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    lowered = text.lower()

    # --- High-priority cart quantity updates (existing cart items only) ---
    if await _handle_cart_quantity_update(message, user_id, lowered):
        return

    # --- High-priority company/about queries (bypass LLM) ---
    about_tokens = ("компан", "лаборатор", "сайт", "адрес", "инн", "кпп")
    if any(token in lowered for token in about_tokens):
        from bot.handlers.faq import handle_about
        await handle_about(message)
        return

    # --- High-priority navigation to cart ---
    # These commands must bypass LLM and route directly to cart.
    if (
        lowered in _CART_NAV_KEYWORDS
        or "корзин" in lowered
        or " cart" in f" {lowered}"
    ):
        from bot.handlers.cart import handle_cart_button
        await handle_cart_button(message)
        return

    # --- High-priority clear cart commands (including common typos) ---
    if (
        lowered in _CLEAR_CART_KEYWORDS
        or ("карзин" in lowered and ("очист" in lowered or "отчист" in lowered))
        or ("корзин" in lowered and ("очист" in lowered or "отчист" in lowered))
    ):
        from bot.services.cart_service import clear_cart
        async with get_session() as session:
            count = await clear_cart(session, user_id)
        await message.answer(
            f"🗑 Корзина очищена (удалено позиций: {count}).",
            reply_markup=main_menu_keyboard(),
        )
        return

    # --- High-priority informational category requests with suggestions ---
    if (
        ("какие" in lowered or "какой" in lowered or "вариант" in lowered)
        and ("анализ" in lowered or "услуг" in lowered)
    ):
        loader = PriceLoader.get()
        topic_map = {
            "почв": "почва",
            "вод": "вода",
            "радиац": "радиолог",
            "гамма": "гамма",
        }
        topic_query = None
        for token, query in topic_map.items():
            if token in lowered:
                topic_query = query
                break
        if topic_query:
            suggestions = loader.search(topic_query, limit=5)
            if suggestions:
                lines = [f"🔎 <b>Похожие услуги по запросу «{text}»:</b>\n"]
                for svc in suggestions:
                    lines.append(
                        f"• {svc.name} — {svc.price:,.2f} руб./{svc.unit}\n"
                        f"  Категория: {svc.category_name}"
                    )
                lines.append("\nНапишите название услуги, и я добавлю ее в корзину.")
                await message.answer("\n".join(lines), parse_mode="HTML")
                return

    # --- High-priority "tell me about analysis" requests with direct similar matches ---
    explain_tokens = ("расскажи про анализ", "что за анализ", "информация об анализе", "расскажи об анализе")
    if any(token in lowered for token in explain_tokens):
        query = (
            lowered.replace("расскажи про анализ", "")
            .replace("расскажи об анализе", "")
            .replace("что за анализ", "")
            .replace("информация об анализе", "")
            .strip(" :,-")
        )
        if query:
            loader = PriceLoader.get()
            results = loader.search(query, limit=5)
            if results:
                lines = [f"ℹ️ <b>Похожие услуги по запросу «{text}»:</b>\n"]
                for svc in results:
                    lines.append(
                        f"🔬 <b>{svc.name}</b>\n"
                        f"   Цена: {svc.price:,.2f} руб. (без НДС) / {svc.unit}\n"
                        f"   Категория: {svc.category_name}\n"
                    )
                lines.append("Напишите название услуги, если нужно добавить ее в корзину.")
                await message.answer("\n".join(lines), parse_mode="HTML")
                return

    # --- High-priority "need analysis" requests: suggest first, do not auto-add ---
    need_tokens = ("мне нужен анализ", "нужен анализ", "хочу анализ", "требуется анализ")
    if any(token in lowered for token in need_tokens):
        query = (
            lowered.replace("мне нужен анализ", "")
            .replace("нужен анализ", "")
            .replace("хочу анализ", "")
            .replace("требуется анализ", "")
            .strip(" :,-")
        )
        if query:
            loader = PriceLoader.get()
            suggestions = loader.search(query, limit=5)
            if suggestions:
                lines = [f"🔎 <b>Нашёл похожие услуги по запросу «{text}»:</b>\n"]
                for svc in suggestions:
                    lines.append(
                        f"• {svc.name} — {svc.price:,.2f} руб./{svc.unit}\n"
                        f"  Категория: {svc.category_name}"
                    )
                lines.append(
                    "\nНапишите точное название/часть названия нужной услуги, "
                    "и я добавлю её в корзину."
                )
                await message.answer("\n".join(lines), parse_mode="HTML")
                return

    # --- Junk text detection ---
    if is_junk_text(text):
        await message.answer(
            "🤔 Не удалось понять ваш запрос.\n"
            "Попробуйте написать, какие анализы вам нужны, "
            "или воспользуйтесь каталогом.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # --- Truncate long messages ---
    cleaned = sanitise_for_llm(text)

    # --- Build cart context for LLM ---
    try:
        async with get_session() as session:
            cart_items = await get_cart_items(session, user_id)
        cart_text = format_cart_for_llm(cart_items)
    except Exception:
        cart_text = ""

    # --- Call LLM ---
    client = get_gigachat_client()
    try:
        intent = await client.recognise_intent(cleaned, cart_text)
    except Exception:
        logger.exception("LLM intent recognition failed")
        await message.answer(
            "🔧 Сервис распознавания временно недоступен.\n"
            "Воспользуйтесь каталогом услуг:",
            reply_markup=categories_keyboard(),
        )
        return

    logger.info(
        "User %d intent: action=%s confidence=%.2f services=%s",
        user_id, intent.action, intent.confidence, [s.service_id for s in intent.services],
    )

    # --- Low confidence handling ---
    if (
        intent.confidence < _CONFIDENCE_THRESHOLD
        and intent.action not in {"unknown", "greet", "faq", "show_category", "start_kp_form"}
    ):
        loader = PriceLoader.get()
        suggestions = loader.search(cleaned, limit=3)
        if suggestions:
            pairs = [(s.id, s.name) for s in suggestions]
            await message.answer(
                "🤔 Не совсем понял. Возможно, вы имели в виду:",
                reply_markup=low_confidence_keyboard(pairs),
            )
        else:
            await message.answer(
                "🤔 Не удалось распознать запрос. Попробуйте каталог:",
                reply_markup=categories_keyboard(),
            )
        return

    # --- Route by action ---
    if intent.action == "greet":
        await message.answer(
            "Здравствуйте! Я помогу подобрать анализы, показать каталог и оформить КП.",
            reply_markup=main_menu_keyboard(),
        )
    elif intent.action == "show_category":
        water_hits = {"вода", "сточ", "питьев", "природн"}
        if any(token in cleaned.lower() for token in water_hits):
            loader = PriceLoader.get()
            water_services = [
                svc for svc in loader.search("вода", limit=5)
                if "вод" in svc.category_name.lower() or "вод" in svc.name.lower()
            ]
            if water_services:
                lines = ["💧 <b>Популярные анализы воды:</b>\n"]
                for svc in water_services:
                    lines.append(f"• {svc.name} — {svc.price:,.2f} руб./{svc.unit}")
                lines.append("\nМогу добавить нужные позиции в корзину по вашему запросу.")
                await message.answer("\n".join(lines), parse_mode="HTML")
            else:
                from bot.handlers.services import handle_catalog_button
                await handle_catalog_button(message)
        else:
            from bot.handlers.services import handle_catalog_button
            await handle_catalog_button(message)
    elif intent.action == "start_kp_form":
        from bot.handlers.cart import handle_create_kp_button
        await handle_create_kp_button(message, state)
    elif intent.action == "add_to_cart":
        await _handle_add_to_cart(message, user_id, intent)
    elif intent.action == "remove_from_cart":
        await _handle_remove_from_cart(message, user_id, intent)
    elif intent.action == "view_cart":
        from bot.handlers.cart import handle_cart_button
        await handle_cart_button(message)
    elif intent.action == "create_kp":
        from bot.handlers.cart import handle_create_kp_button
        await handle_create_kp_button(message, state)
    elif intent.action == "explain":
        await _handle_explain(message, intent)
    elif intent.action == "faq":
        from bot.handlers.faq import handle_faq
        await handle_faq(message)
    elif intent.action == "catalog":
        from bot.handlers.services import handle_catalog_button
        await handle_catalog_button(message)
    elif intent.action == "clear_cart":
        from bot.services.cart_service import clear_cart
        async with get_session() as session:
            await clear_cart(session, user_id)
        await message.answer("🗑 Корзина очищена.", reply_markup=main_menu_keyboard())
    elif intent.action == "repeat_order":
        from bot.handlers.cart import handle_repeat_order
        await handle_repeat_order(message)
    else:
        loader = PriceLoader.get()
        suggestions = loader.search(cleaned, limit=3)
        if suggestions:
            pairs = [(s.id, s.name) for s in suggestions]
            await message.answer(
                "🤔 Не совсем понял. Возможно, вас интересует:",
                reply_markup=low_confidence_keyboard(pairs),
            )
        else:
            await message.answer(
                "Не удалось распознать запрос. Воспользуйтесь каталогом:",
                reply_markup=categories_keyboard(),
            )


async def _handle_add_to_cart(message: Message, user_id: int, intent) -> None:
    """Add services from LLM intent to cart."""
    if not intent.services:
        await message.answer(
            "Не удалось определить конкретные услуги. Попробуйте каталог:",
            reply_markup=categories_keyboard(),
        )
        return

    loader = PriceLoader.get()
    added_names: list[str] = []

    try:
        async with get_session() as session:
            for svc_match in intent.services:
                svc = loader.get_service(svc_match.service_id)
                if svc:
                    await add_item(session, user_id, svc_match.service_id, svc_match.quantity)
                    added_names.append(f"• {svc.name} ×{svc_match.quantity}")
    except Exception:
        logger.exception("Error adding items from LLM intent")
        await message.answer("❌ Ошибка при добавлении в корзину.")
        return

    if added_names:
        text = "✅ Добавлено в корзину:\n" + "\n".join(added_names)
        text += "\n\nИспользуйте 🛒 Корзина для просмотра."
        await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    else:
        await message.answer(
            "Не удалось найти указанные услуги в прейскуранте.",
            reply_markup=categories_keyboard(),
        )


async def _handle_remove_from_cart(message: Message, user_id: int, intent) -> None:
    """Remove services from cart based on LLM intent."""
    from bot.services.cart_service import remove_item

    if not intent.services:
        await message.answer("Укажите, какую услугу удалить из корзины.")
        return

    removed: list[str] = []
    try:
        async with get_session() as session:
            for svc_match in intent.services:
                ok = await remove_item(session, user_id, svc_match.service_id)
                if ok:
                    loader = PriceLoader.get()
                    svc = loader.get_service(svc_match.service_id)
                    removed.append(svc.name if svc else svc_match.service_id)
    except Exception:
        logger.exception("Error removing items from LLM intent")

    if removed:
        text = "🗑 Удалено из корзины:\n" + "\n".join(f"• {n}" for n in removed)
        await message.answer(text, reply_markup=main_menu_keyboard())
    else:
        await message.answer("Указанные услуги не найдены в корзине.")


async def _handle_explain(message: Message, intent) -> None:
    """Provide explanation about a service or query."""
    query = intent.explanation_query or (message.text or "")

    loader = PriceLoader.get()
    results = loader.search(query, limit=3)

    if results:
        lines = ["ℹ️ <b>По вашему запросу найдено:</b>\n"]
        for svc in results:
            lines.append(
                f"🔬 <b>{svc.name}</b>\n"
                f"   Цена: {svc.price:,.2f} руб. (без НДС) / {svc.unit}\n"
                f"   Категория: {svc.category_name}\n"
            )
        lines.append("Для добавления в корзину используйте каталог или напишите запрос.")
        await message.answer("\n".join(lines), parse_mode="HTML")
    else:
        await message.answer(
            "ℹ️ К сожалению, по вашему запросу ничего не найдено.\n"
            "Попробуйте другую формулировку или откройте каталог.",
            reply_markup=categories_keyboard(),
        )


async def _handle_cart_quantity_update(message: Message, user_id: int, lowered_text: str) -> bool:
    """Update quantity for items that already exist in cart."""
    match = re.search(r"(?:позици[яию]\s*(\d+).{0,20})?(\d+)\s*шт", lowered_text)
    if not match:
        return False

    position_num = int(match.group(1)) if match.group(1) else None
    target_qty = int(match.group(2))
    if target_qty <= 0:
        return False

    async with get_session() as session:
        items = await get_cart_items(session, user_id)
        if not items:
            await message.answer("🛒 Корзина пуста. Сначала добавьте услуги.")
            return True

        target_item = None
        if position_num is not None:
            idx = position_num - 1
            if 0 <= idx < len(items):
                target_item = items[idx]
        else:
            query = re.sub(
                r"(сделай|поставь|измени|измени|кол-?во|количество|шт|на|\d+|позици[яию])",
                " ",
                lowered_text,
            )
            query = " ".join(query.split())
            if query:
                ranked = sorted(
                    items,
                    key=lambda it: PriceLoader.get().search(query, limit=1)[0].name
                    if PriceLoader.get().search(query, limit=1)
                    else "",
                )
                # robust local matching against cart names
                by_name = sorted(
                    items,
                    key=lambda it: (
                        0 if query and query in it.service_name.lower() else 1,
                        len(it.service_name),
                    ),
                )
                target_item = by_name[0] if by_name and query in by_name[0].service_name.lower() else None

        if target_item is None:
            await message.answer(
                "Не нашёл такую позицию в корзине. Укажите номер, например: «позиция 2 — 3 шт»."
            )
            return True

        updated = await set_item_quantity(session, user_id, target_item.service_id, target_qty)
        if updated is None:
            await message.answer("Не удалось обновить количество. Позиция не найдена в корзине.")
            return True

        await message.answer(
            f"✅ Обновил количество:\n"
            f"{updated.service_name} → {updated.quantity} шт."
        )
        return True
