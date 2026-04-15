from __future__ import annotations

from bot.handlers.kp_form import _looks_like_inn, _next_step_after_autofill
from bot.states.kp_form import KPForm


def test_next_step_after_autofill_missing_org_name():
    step = _next_step_after_autofill({"kpp": "123456789", "address": "СПб"})
    assert step == KPForm.org_name


def test_next_step_after_autofill_missing_kpp():
    step = _next_step_after_autofill({"org_name": 'ООО "Тест"', "address": "СПб"})
    assert step == KPForm.kpp


def test_next_step_after_autofill_missing_address():
    step = _next_step_after_autofill({"org_name": 'ООО "Тест"', "kpp": "123456789"})
    assert step == KPForm.address


def test_next_step_after_autofill_complete_company_fields():
    step = _next_step_after_autofill(
        {"org_name": 'ООО "Тест"', "kpp": "123456789", "address": "СПб"}
    )
    assert step == KPForm.contact_person


def test_looks_like_inn():
    assert _looks_like_inn("7813096076")
    assert _looks_like_inn("123456789012")
    assert not _looks_like_inn("ООО Снарк")
