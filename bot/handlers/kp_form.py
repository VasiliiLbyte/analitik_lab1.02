"""Handler: KP Form FSM — 7 data steps + preview + generate + send.

Steps:
  1. inn           — ИНН (10/12 digits, checksum validated)
  2. org_name      — Organisation name
  3. kpp           — КПП (9 digits)
  4. address       — Legal address
  5. contact_person — Contact person full name
  6. contact_info  — Phone or email
  7. sample_location — Factual sample location
  preview            — Review all data -> confirm / edit / cancel

Protection scenarios:
  - Invalid data at each step -> re-prompt with explanation
  - "отмена" / "назад" / /start at any step -> handle gracefully
  - Word generation failure -> error message + logging
  - Auto-clear cart after successful KP send
"""

from __future__ import annotations

import datetime
import logging
import re

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import func, select

from bot.database.models import Order, OrderItem
from bot.database.session import get_session
from bot.keyboards.main import (
    back_cancel_keyboard,
    confirm_preview_keyboard,
    inn_autofill_keyboard,
    main_menu_keyboard,
)
from bot.services.cart_service import clear_cart, get_cart_items, get_cart_summary, get_or_create_user
from bot.services import bitrix_service
from bot.services.company_lookup import lookup_company_by_inn
from bot.services.kp_generator import build_kp_data, generate_kp
from bot.states.kp_form import KPForm
from bot.utils.validators import (
    validate_address,
    validate_contact_info,
    validate_contact_person,
    validate_inn,
    validate_kpp,
    validate_org_name,
)

router = Router(name="kp_form")
logger = logging.getLogger(__name__)

_CANCEL_WORDS = {"отмена", "отменить", "cancel", "стоп", "выход"}
_BACK_WORDS = {"назад", "back"}
_INN_LIKE_RE = re.compile(r"^\d{10}(\d{2})?$")

_STEP_ORDER: list = [
    KPForm.inn,
    KPForm.org_name,
    KPForm.kpp,
    KPForm.address,
    KPForm.contact_person,
    KPForm.contact_info,
    KPForm.sample_location,
    KPForm.preview,
]

_STEP_PROMPTS = {
    KPForm.inn: "<b>Шаг 1/7:</b> Введите ИНН (10 или 12 цифр):",
    KPForm.org_name: "<b>Шаг 2/7:</b> Введите название организации заказчика:",
    KPForm.kpp: "<b>Шаг 3/7:</b> Введите КПП (9 цифр):",
    KPForm.address: "<b>Шаг 4/7:</b> Введите юридический адрес:",
    KPForm.contact_person: "<b>Шаг 5/7:</b> Введите ФИО контактного лица:",
    KPForm.contact_info: "<b>Шаг 6/7:</b> Введите телефон или email:",
    KPForm.sample_location: (
        "<b>Шаг 7/7:</b> Введите фактическое местоположение объекта отбора проб:"
    ),
}

_VALIDATORS = {
    KPForm.org_name: ("org_name", validate_org_name),
    KPForm.inn: ("inn", validate_inn),
    KPForm.kpp: ("kpp", validate_kpp),
    KPForm.address: ("address", validate_address),
    KPForm.contact_person: ("contact_person", validate_contact_person),
    KPForm.contact_info: ("contact_info", validate_contact_info),
    KPForm.sample_location: ("sample_location", validate_address),
}


def _resolve_next_step_from(current_step) -> object:
    idx = _STEP_ORDER.index(current_step)
    return _STEP_ORDER[idx + 1]


def _next_step_after_autofill(data: dict) -> object:
    if not data.get("org_name"):
        return KPForm.org_name
    if not data.get("kpp"):
        return KPForm.kpp
    if not data.get("address"):
        return KPForm.address
    return KPForm.contact_person


def _looks_like_inn(text: str) -> bool:
    return _INN_LIKE_RE.fullmatch(text.strip()) is not None


async def _go_to_step(message: Message, state: FSMContext, step_state) -> None:
    await state.set_state(step_state)
    if step_state == KPForm.preview:
        await _show_preview(message, state)
        return
    prompt = _STEP_PROMPTS[step_state]
    await message.answer(prompt, reply_markup=back_cancel_keyboard(), parse_mode="HTML")


async def _handle_cancel(message: Message, state: FSMContext) -> bool:
    """Check for cancel/back words. Returns True if handled."""
    text = (message.text or "").strip().lower()
    if text in _CANCEL_WORDS:
        await state.clear()
        await message.answer(
            "❌ Формирование КП отменено.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return True
    if text in _BACK_WORDS:
        return await _go_back(message, state)
    return False


async def _go_back(message: Message, state: FSMContext) -> bool:
    """Go to the previous step."""
    current = await state.get_state()
    if current is None:
        return False

    idx = None
    for i, s in enumerate(_STEP_ORDER):
        if s.state == current:
            idx = i
            break

    if idx is None or idx == 0:
        await state.clear()
        await message.answer(
            "❌ Формирование КП отменено.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return True

    prev_state = _STEP_ORDER[idx - 1]
    await state.set_state(prev_state)
    prompt = _STEP_PROMPTS.get(prev_state, "Введите данные:")
    await message.answer(prompt, reply_markup=back_cancel_keyboard(), parse_mode="HTML")
    return True


# --- Step handlers ---

@router.message(
    StateFilter(
        KPForm.org_name,
        KPForm.inn,
        KPForm.kpp,
        KPForm.address,
        KPForm.contact_person,
        KPForm.contact_info,
        KPForm.sample_location,
    )
)
async def handle_kp_step(message: Message, state: FSMContext) -> None:
    """Universal handler for all KP form data steps."""
    if await _handle_cancel(message, state):
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Пожалуйста, введите данные (текст не может быть пустым).")
        return

    current = await state.get_state()
    if current is None:
        return

    # Find current step config
    step_state = None
    for s in _STEP_ORDER:
        if s.state == current:
            step_state = s
            break
    if step_state is None or step_state not in _VALIDATORS:
        return

    field_name, validator = _VALIDATORS[step_state]

    if step_state == KPForm.org_name and _looks_like_inn(text):
        await message.answer(
            "⚠️ Похоже, вы ввели ИНН.\n"
            "Нажмите «Назад» или перезапустите форму, чтобы начать с ИНН.",
            reply_markup=back_cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    ok, result = validator(text)
    if not ok:
        await message.answer(
            f"⚠️ {result}\n\nПопробуйте ещё раз или напишите «отмена».",
            reply_markup=back_cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.update_data(**{field_name: result})

    if step_state == KPForm.inn:
        company = await lookup_company_by_inn(result)
        if company:
            await state.update_data(
                inn_autofill_candidate={
                    "org_name": company.name,
                    "kpp": company.kpp,
                    "address": company.address,
                    "ogrn": company.ogrn,
                    "status": company.status,
                }
            )
            status_text = f"\nСтатус: {company.status}" if company.status else ""
            kpp_text = company.kpp or "—"
            address_text = company.address or "—"
            await message.answer(
                "🔎 Нашёл реквизиты по ИНН:\n\n"
                f"<b>Организация:</b> {company.name}\n"
                f"<b>КПП:</b> {kpp_text}\n"
                f"<b>Адрес:</b> {address_text}"
                f"{status_text}\n\n"
                "Подтвердите автозаполнение или перейдите к ручному вводу.",
                reply_markup=inn_autofill_keyboard(),
                parse_mode="HTML",
            )
            return

    # Move to next step
    next_state = _resolve_next_step_from(step_state)
    await _go_to_step(message, state, next_state)


async def _show_preview(message: Message, state: FSMContext, user_id: int | None = None) -> None:
    """Show preview of all entered data for confirmation."""
    data = await state.get_data()

    if user_id is None:
        user_id = message.from_user.id  # type: ignore[union-attr]
    try:
        async with get_session() as session:
            summary = await get_cart_summary(session, user_id)
    except Exception:
        await message.answer("❌ Ошибка загрузки корзины.")
        return

    preview = (
        "📄 <b>Предпросмотр КП</b>\n\n"
        f"<b>Организация:</b> {data.get('org_name', '—')}\n"
        f"<b>ИНН:</b> {data.get('inn', '—')}\n"
        f"<b>КПП:</b> {data.get('kpp', '—')}\n"
        f"<b>Адрес:</b> {data.get('address', '—')}\n"
        f"<b>Контактное лицо:</b> {data.get('contact_person', '—')}\n"
        f"<b>Телефон/Email:</b> {data.get('contact_info', '—')}\n\n"
        f"<b>Местоположение объекта:</b> {data.get('sample_location', '—')}\n"
        "\n"
        f"<b>Позиций в корзине:</b> {summary.item_count}\n"
        f"<b>Сметная стоимость:</b> {summary.subtotal:,.2f} руб.\n"
        f"<b>Оформление протоколов (3%):</b> {summary.protocol_fee:,.2f} руб.\n"
        f"<b>НДС (5%):</b> {summary.nds:,.2f} руб.\n"
        f"<b>ИТОГО:</b> {summary.total:,.2f} руб.\n\n"
        "Подтвердите данные или внесите изменения:"
    )
    await message.answer(preview, reply_markup=confirm_preview_keyboard(), parse_mode="HTML")


@router.message(StateFilter(KPForm.preview))
async def handle_preview_text(message: Message, state: FSMContext) -> None:
    """Handle text input during preview — check for cancel/back, otherwise re-show."""
    text = (message.text or "").strip().lower()
    if text in _CANCEL_WORDS:
        await state.clear()
        await message.answer(
            "❌ Формирование КП отменено.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return
    if text in _BACK_WORDS:
        await state.set_state(KPForm.sample_location)
        await message.answer(
            _STEP_PROMPTS[KPForm.sample_location],
            reply_markup=back_cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        "⬆️ Используйте кнопки выше для подтверждения, редактирования или отмены.",
        reply_markup=confirm_preview_keyboard(),
    )


# --- Preview callbacks ---

@router.callback_query(F.data == "kp_confirm", StateFilter(KPForm.preview))
async def callback_kp_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    """Generate KP document and send to user."""
    user_id = callback.from_user.id
    data = await state.get_data()

    await callback.message.answer("⏳ Генерирую документ...")  # type: ignore[union-attr]

    try:
        async with get_session() as session:
            user = await get_or_create_user(session, user_id)
            cart_items = await get_cart_items(session, user_id)
            summary = await get_cart_summary(session, user_id)

            if not cart_items:
                await callback.message.answer("❌ Корзина пуста.")  # type: ignore[union-attr]
                await state.clear()
                await callback.answer()
                return

            count_result = await session.execute(
                select(func.count(Order.id)).where(Order.user_id == user.id)
            )
            order_count = count_result.scalar() or 0
            kp_number = f"НФ-{order_count + 1:03d}"
            kp_date = datetime.date.today()

            kp_data = build_kp_data(
                kp_number=kp_number,
                kp_date=kp_date,
                customer_name=data["org_name"],
                customer_inn=data["inn"],
                customer_kpp=data["kpp"],
                customer_address=data["address"],
                contact_person=data["contact_person"],
                contact_info=data["contact_info"],
                sample_location=data.get("sample_location", ""),
                cart_items=cart_items,
            )

            file_path = generate_kp(kp_data)

            # Save order to DB
            order = Order(
                user_id=user.id,
                number=kp_number,
                date=kp_date,
                org_name=data["org_name"],
                inn=data["inn"],
                kpp=data["kpp"],
                address=data["address"],
                contact_person=data["contact_person"],
                contact_info=data["contact_info"],
                subtotal=summary.subtotal,
                protocol_fee=summary.protocol_fee,
                nds=summary.nds,
                total=summary.total,
                total_words=kp_data.total_words,
                status="completed",
                file_path=str(file_path),
            )
            session.add(order)
            await session.flush()

            for item in cart_items:
                oi = OrderItem(
                    order_id=order.id,
                    service_id=item.service_id,
                    service_name=item.service_name,
                    category_name=item.category_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    unit_price=item.unit_price,
                    total_price=round(item.unit_price * item.quantity, 2),
                )
                session.add(oi)

            user.last_order_id = order.id
            await session.flush()

            # Clear cart after successful KP generation
            await clear_cart(session, user_id)

        # Send file to user
        doc = FSInputFile(str(file_path), filename=f"КП_{kp_number}_{kp_date}.docx")
        await callback.message.answer_document(  # type: ignore[union-attr]
            doc,
            caption=(
                f"✅ Коммерческое предложение <b>№ {kp_number}</b> "
                f"от {kp_date.strftime('%d.%m.%Y')}\n"
                f"Итого: <b>{summary.total:,.2f} руб.</b> (с НДС 5%)\n\n"
                "Корзина очищена. Для нового заказа используйте каталог."
            ),
            parse_mode="HTML",
        )

        try:
            client_data = {
                "company_name": data.get("org_name", ""),
                "fio": data.get("contact_person", ""),
                "inn": data.get("inn", ""),
                "kpp": data.get("kpp", ""),
                "address": data.get("address", ""),
                "contact_person": data.get("contact_person", ""),
                "contact_info": data.get("contact_info", ""),
                "sample_location": data.get("sample_location", ""),
            }
            deal_id = await bitrix_service.create_lab_item(
                client_data=client_data,
                cart_items=cart_items,
                total_sum=float(summary.total),
                kp_number=kp_number,
            )
            if deal_id:
                async with get_session() as session:
                    saved_order = await session.get(Order, order.id)
                    if saved_order is not None:
                        saved_order.bitrix_item_id = int(deal_id)
                await callback.message.answer(  # type: ignore[union-attr]
                    f"✅ Карточка в Bitrix24 создана (ID: {deal_id})"
                )
        except Exception:
            logger.exception("Failed to create Bitrix24 item for KP %s", kp_number)

        await callback.message.answer(  # type: ignore[union-attr]
            "Главное меню:", reply_markup=main_menu_keyboard()
        )

    except Exception:
        logger.exception("Failed to generate KP for user %d", user_id)
        await callback.message.answer(  # type: ignore[union-attr]
            "❌ Ошибка при генерации документа. Попробуйте позже или обратитесь в поддержку.",
            reply_markup=main_menu_keyboard(),
        )

    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "kp_edit", StateFilter(KPForm.preview))
async def callback_kp_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Go back to step 1 for editing."""
    await state.set_state(KPForm.inn)
    data = await state.get_data()
    current_inn = data.get("inn", "")
    await callback.message.answer(  # type: ignore[union-attr]
        f"✏️ Текущее значение: {current_inn}\n\n"
        f"{_STEP_PROMPTS[KPForm.inn]}",
        reply_markup=back_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "kp_cancel")
async def callback_kp_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel the KP form from any step."""
    await state.clear()
    await callback.message.answer(  # type: ignore[union-attr]
        "❌ Формирование КП отменено. Корзина сохранена.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "inn_autofill:accept", StateFilter(KPForm.inn))
async def callback_inn_autofill_accept(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    candidate = data.get("inn_autofill_candidate") or {}
    if not candidate:
        await callback.message.answer(  # type: ignore[union-attr]
            "Не удалось получить данные автозаполнения. Продолжим вручную."
        )
        await _go_to_step(callback.message, state, KPForm.org_name)  # type: ignore[arg-type]
        await callback.answer()
        return

    updates: dict[str, str] = {}
    if candidate.get("org_name"):
        ok, value = validate_org_name(candidate["org_name"])
        if ok:
            updates["org_name"] = value
    if candidate.get("kpp"):
        ok, value = validate_kpp(candidate["kpp"])
        if ok:
            updates["kpp"] = value
    if candidate.get("address"):
        ok, value = validate_address(candidate["address"])
        if ok:
            updates["address"] = value

    await state.update_data(**updates, inn_autofill_candidate=None)

    current_data = await state.get_data()
    next_step = _next_step_after_autofill(current_data)

    await callback.message.answer(  # type: ignore[union-attr]
        "✅ Реквизиты применены. При необходимости их можно отредактировать через кнопку «Редактировать» на предпросмотре."
    )
    await _go_to_step(callback.message, state, next_step)  # type: ignore[arg-type]
    await callback.answer()


@router.callback_query(F.data == "inn_autofill:manual", StateFilter(KPForm.inn))
async def callback_inn_autofill_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(inn_autofill_candidate=None)
    await callback.message.answer(  # type: ignore[union-attr]
        "Хорошо, продолжим ввод реквизитов вручную."
    )
    await _go_to_step(callback.message, state, KPForm.org_name)  # type: ignore[arg-type]
    await callback.answer()


@router.callback_query(F.data == "kp_back")
async def callback_kp_back(callback: CallbackQuery, state: FSMContext) -> None:
    """Go back one step via inline button."""
    current = await state.get_state()
    if current is None:
        await callback.answer()
        return

    idx = None
    for i, s in enumerate(_STEP_ORDER):
        if s.state == current:
            idx = i
            break

    if idx is None or idx == 0:
        await state.clear()
        await callback.message.answer(  # type: ignore[union-attr]
            "❌ Формирование КП отменено.",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return

    prev_state = _STEP_ORDER[idx - 1]
    await state.set_state(prev_state)
    data = await state.get_data()

    field_name, _ = _VALIDATORS.get(prev_state, (None, None))
    current_val = data.get(field_name, "") if field_name else ""
    prompt = _STEP_PROMPTS.get(prev_state, "Введите данные:")

    text = f"✏️ Текущее значение: {current_val}\n\n{prompt}" if current_val else prompt
    await callback.message.answer(  # type: ignore[union-attr]
        text, reply_markup=back_cancel_keyboard(), parse_mode="HTML"
    )
    await callback.answer()
