from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.handlers import kp_form
from bot.states.kp_form import KPForm


class _DummyState:
    def __init__(self, state: str | None = None, data: dict | None = None):
        self._state = state
        self._data = data or {}
        self.cleared = False

    async def set_state(self, state_obj):
        self._state = state_obj.state if hasattr(state_obj, "state") else state_obj

    async def get_state(self):
        return self._state

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)

    async def clear(self):
        self._state = None
        self._data = {}
        self.cleared = True


class _DummyMessage:
    def __init__(self, text: str = "", user_id: int = 1):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)


class _DummyCallback:
    def __init__(self, data: str, message: _DummyMessage, user_id: int = 1):
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=user_id)
        self._answered = False

    async def answer(self, *args, **kwargs):
        self._answered = True


@pytest.mark.asyncio
async def test_kp_edit_moves_preview_to_inn_step():
    state = _DummyState(
        KPForm.preview.state,
        {"inn": "7806341520", "org_name": 'ООО "Тест"'},
    )
    message = _DummyMessage()
    callback = _DummyCallback("kp_edit", message)

    await kp_form.callback_kp_edit(callback, state)

    assert await state.get_state() == KPForm.inn.state
    assert message.answers
    assert "Шаг 1/7" in message.answers[0]
    assert "ИНН" in message.answers[0]


@pytest.mark.asyncio
async def test_preview_text_back_returns_to_sample_location_step():
    state = _DummyState(KPForm.preview.state)
    message = _DummyMessage(text="назад")

    await kp_form.handle_preview_text(message, state)

    assert await state.get_state() == KPForm.sample_location.state
    assert message.answers
    assert "Шаг 7/7" in message.answers[0]


@pytest.mark.asyncio
async def test_kp_back_from_org_name_moves_to_inn():
    state = _DummyState(
        KPForm.org_name.state,
        {"inn": "7806341520", "org_name": 'ООО "Тест"'},
    )
    message = _DummyMessage()
    callback = _DummyCallback("kp_back", message)

    await kp_form.callback_kp_back(callback, state)

    assert await state.get_state() == KPForm.inn.state
    assert message.answers
    assert "Шаг 1/7" in message.answers[0]


@pytest.mark.asyncio
async def test_kp_back_from_inn_cancels_form():
    state = _DummyState(KPForm.inn.state)
    message = _DummyMessage()
    callback = _DummyCallback("kp_back", message)

    await kp_form.callback_kp_back(callback, state)

    assert await state.get_state() is None
    assert message.answers
    assert "Формирование КП отменено" in message.answers[0]


@pytest.mark.asyncio
async def test_cancel_word_clears_form_on_data_step():
    state = _DummyState(KPForm.inn.state)
    message = _DummyMessage(text="отмена")

    await kp_form.handle_kp_step(message, state)

    assert await state.get_state() is None
    assert "Формирование КП отменено" in message.answers[0]


@pytest.mark.asyncio
async def test_kp_cancel_callback_clears_form():
    state = _DummyState(KPForm.contact_info.state)
    message = _DummyMessage()
    callback = _DummyCallback("kp_cancel", message)

    await kp_form.callback_kp_cancel(callback, state)

    assert await state.get_state() is None
    assert message.answers
    assert "Формирование КП отменено" in message.answers[0]
