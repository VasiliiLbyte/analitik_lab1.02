"""Commercial Proposal (КП) Word document generator.

Generates .docx files matching the exact format from the PDF examples:
  - Header: "Коммерческое предложение № XX от DD MMMM YYYY г."
  - Executor: full legal details of ООО "АНАЛИТИК.ЛАБ"
  - Customer: data from KP form
  - Table: services grouped by category with sub-totals
  - Protocol fee (3%), NDS (5%), Grand total
  - Total in words via num2words

Two-phase approach:
  1. create_template() — builds the .docx template with jinja2 tags (run once)
  2. generate_kp() — renders the template with actual order data
"""

from __future__ import annotations

import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from docxtpl import DocxTemplate
from num2words import num2words

from bot.config import GENERATED_DIR, TEMPLATES_DIR, get_settings

logger = logging.getLogger(__name__)

_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


@dataclass
class KPLineItem:
    number: int
    name: str
    quantity: int
    unit: str
    unit_price: float
    total_price: float


@dataclass
class KPCategoryGroup:
    name: str
    items: list[KPLineItem] = field(default_factory=list)
    subtotal: float = 0.0


@dataclass
class KPData:
    kp_number: str
    kp_date: datetime.date
    customer_name: str
    customer_inn: str
    customer_kpp: str
    customer_address: str
    contact_person: str
    contact_info: str
    groups: list[KPCategoryGroup] = field(default_factory=list)
    subtotal: float = 0.0
    protocol_fee: float = 0.0
    total_before_nds: float = 0.0
    nds: float = 0.0
    total: float = 0.0
    total_words: str = ""
    total_items: int = 0


def _format_date_ru(d: datetime.date) -> str:
    return f"{d.day} {_MONTHS_RU[d.month]} {d.year} г."


def _amount_in_words(amount: float) -> str:
    """Convert amount to Russian words: 'Тридцать семь тысяч ... рублей XX копеек'."""
    rubles = int(amount)
    kopecks = round((amount - rubles) * 100)

    rubles_text = num2words(rubles, lang="ru").capitalize()

    ruble_word = _decline_rubles(rubles)
    kopeck_word = _decline_kopecks(kopecks)

    return f"{rubles_text} {ruble_word} {kopecks:02d} {kopeck_word}"


def _decline_rubles(n: int) -> str:
    last2 = n % 100
    last1 = n % 10
    if 11 <= last2 <= 19:
        return "рублей"
    if last1 == 1:
        return "рубль"
    if 2 <= last1 <= 4:
        return "рубля"
    return "рублей"


def _decline_kopecks(n: int) -> str:
    last2 = n % 100
    last1 = n % 10
    if 11 <= last2 <= 19:
        return "копеек"
    if last1 == 1:
        return "копейка"
    if 2 <= last1 <= 4:
        return "копейки"
    return "копеек"


def create_template() -> Path:
    """Programmatically create the KP docx template with jinja2 tags."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    template_path = TEMPLATES_DIR / "kp_template.docx"

    doc = Document()

    style = doc.styles["Normal"]
    font = style.font
    font.name = "Times New Roman"
    font.size = Pt(11)

    for section in doc.sections:
        section.top_margin = Cm(1.5)
        section.bottom_margin = Cm(1.5)
        section.left_margin = Cm(2)
        section.right_margin = Cm(1.5)

    # Title
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Коммерческое предложение № {{ kp_number }} от {{ kp_date }}")
    run.bold = True
    run.font.size = Pt(14)

    # Executor
    p = doc.add_paragraph()
    run = p.add_run("Исполнитель:")
    run.bold = True
    doc.add_paragraph("{{ executor_full }}")

    # Customer
    p = doc.add_paragraph()
    run = p.add_run("Заказчик:")
    run.bold = True
    doc.add_paragraph("{{ customer_full }}")

    # Table header
    table = doc.add_table(rows=1, cols=6)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"

    headers = ["№", "Товары (работы, услуги)", "Кол-во", "Ед.", "Цена", "Сумма"]
    for i, header_text in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = header_text
        for paragraph in cell.paragraphs:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in paragraph.runs:
                run.bold = True
                run.font.size = Pt(10)

    # Jinja loop for category groups
    row = table.add_row()
    row.cells[0].text = ""
    row.cells[1].text = (
        "{%tr for group in groups %}"
        "{{ group.name }}"
    )
    row.cells[5].text = "{{ group.subtotal }}"

    row2 = table.add_row()
    row2.cells[0].text = "{%tr for item in group.items %}\n{{ item.number }}"
    row2.cells[1].text = "{{ item.name }}"
    row2.cells[2].text = "{{ item.quantity }}"
    row2.cells[3].text = "{{ item.unit }}"
    row2.cells[4].text = "{{ item.unit_price }}"
    row2.cells[5].text = "{{ item.total_price }}\n{%tr endfor %}\n{%tr endfor %}"

    # Totals
    doc.add_paragraph("")
    doc.add_paragraph("Итого: {{ subtotal_str }} руб.")
    doc.add_paragraph("Оформление протоколов (3%): {{ protocol_fee_str }} руб.")
    doc.add_paragraph("Итого с оформлением: {{ total_before_nds_str }} руб.")
    doc.add_paragraph("Кроме того НДС (5%): {{ nds_str }} руб.")

    p = doc.add_paragraph()
    run = p.add_run("Всего наименований {{ total_items }}, на сумму {{ total_str }} руб.")
    run.bold = True

    doc.add_paragraph("{{ total_words }}")

    doc.save(str(template_path))
    logger.info("KP template created at %s", template_path)
    return template_path


def build_kp_data(
    kp_number: str,
    kp_date: datetime.date,
    customer_name: str,
    customer_inn: str,
    customer_kpp: str,
    customer_address: str,
    contact_person: str,
    contact_info: str,
    cart_items: list,
) -> KPData:
    """Build KPData from cart items, grouping by category with auto protocol fee."""
    settings = get_settings()

    grouped: dict[str, list] = defaultdict(list)
    for item in cart_items:
        grouped[item.category_name].append(item)

    groups: list[KPCategoryGroup] = []
    global_num = 0
    subtotal = 0.0
    total_items = 0

    for cat_name, items in grouped.items():
        group = KPCategoryGroup(name=cat_name)
        cat_subtotal = 0.0
        for item in items:
            global_num += 1
            total_items += item.quantity
            line_total = round(item.unit_price * item.quantity, 2)
            cat_subtotal += line_total
            group.items.append(KPLineItem(
                number=global_num,
                name=item.service_name,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                total_price=line_total,
            ))
        group.subtotal = round(cat_subtotal, 2)
        subtotal += cat_subtotal
        groups.append(group)

    subtotal = round(subtotal, 2)
    protocol_fee = round(subtotal * settings.protocol_fee_rate / 100, 2)
    total_before_nds = round(subtotal + protocol_fee, 2)
    nds = round(total_before_nds * settings.nds_rate / 100, 2)
    total = round(total_before_nds + nds, 2)

    return KPData(
        kp_number=kp_number,
        kp_date=kp_date,
        customer_name=customer_name,
        customer_inn=customer_inn,
        customer_kpp=customer_kpp,
        customer_address=customer_address,
        contact_person=contact_person,
        contact_info=contact_info,
        groups=groups,
        subtotal=subtotal,
        protocol_fee=protocol_fee,
        total_before_nds=total_before_nds,
        nds=nds,
        total=total,
        total_words=_amount_in_words(total),
        total_items=total_items,
    )


def _fmt(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def generate_kp(data: KPData) -> Path:
    """Render the KP template with order data and return path to generated .docx."""
    template_path = TEMPLATES_DIR / "kp_template.docx"
    if not template_path.exists():
        create_template()

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = GENERATED_DIR / f"KP_{data.kp_number}_{data.kp_date.isoformat()}.docx"

    settings = get_settings()

    executor_full = (
        f'{settings.executor_name}, ИНН {settings.executor_inn}, '
        f'КПП {settings.executor_kpp}, {settings.executor_address}'
    )
    customer_full = (
        f'{data.customer_name}, ИНН {data.customer_inn}, '
        f'КПП {data.customer_kpp}, {data.customer_address}'
    )

    # Prepare template-friendly group dicts
    groups_ctx = []
    for g in data.groups:
        groups_ctx.append({
            "name": g.name,
            "subtotal": _fmt(g.subtotal),
            "items": [
                {
                    "number": it.number,
                    "name": it.name,
                    "quantity": it.quantity,
                    "unit": it.unit,
                    "unit_price": _fmt(it.unit_price),
                    "total_price": _fmt(it.total_price),
                }
                for it in g.items
            ],
        })

    context = {
        "kp_number": data.kp_number,
        "kp_date": _format_date_ru(data.kp_date),
        "executor_full": executor_full,
        "customer_full": customer_full,
        "groups": groups_ctx,
        "subtotal_str": _fmt(data.subtotal),
        "protocol_fee_str": _fmt(data.protocol_fee),
        "total_before_nds_str": _fmt(data.total_before_nds),
        "nds_str": _fmt(data.nds),
        "total_str": _fmt(data.total),
        "total_items": data.total_items,
        "total_words": data.total_words,
    }

    try:
        tpl = DocxTemplate(str(template_path))
        tpl.render(context)
        tpl.save(str(output_path))
        logger.info("KP generated: %s", output_path)
        return output_path
    except Exception:
        logger.exception("Failed to generate KP document")
        raise
