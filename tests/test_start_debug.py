from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.enums import ChatType

from bot.handlers import start
from bot.handlers.start import _parse_admin_ids


def test_parse_admin_ids_handles_csv_and_spaces():
    assert _parse_admin_ids("1, 2,3") == {1, 2, 3}


def test_parse_admin_ids_ignores_invalid_values():
    assert _parse_admin_ids("42,abc,, -7, 100") == {42, 100}


class _DummyState:
    async def get_state(self):
        return "kp_form:inn"

    async def get_data(self):
        return {
            "inn": "7806341520",
            "org_name": 'ООО "Тест"',
            "kpp": "781601001",
        }


class _DummyMessage:
    def __init__(self, user_id: int, chat_type: ChatType):
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(type=chat_type)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)


@pytest.mark.asyncio
async def test_debug_state_allowed_only_in_private_chat(monkeypatch):
    monkeypatch.setattr(
        start,
        "get_settings",
        lambda: SimpleNamespace(admin_user_ids="1", app_version="test-version"),
    )

    message = _DummyMessage(user_id=1, chat_type=ChatType.GROUP)
    await start.cmd_debug_state(message, _DummyState())

    assert message.answers
    assert "только в личном чате" in message.answers[0].lower()


@pytest.mark.asyncio
async def test_debug_state_private_admin_receives_state(monkeypatch):
    monkeypatch.setattr(
        start,
        "get_settings",
        lambda: SimpleNamespace(admin_user_ids="1", app_version="test-version"),
    )

    message = _DummyMessage(user_id=1, chat_type=ChatType.PRIVATE)
    await start.cmd_debug_state(message, _DummyState())

    assert message.answers
    assert "Debug state" in message.answers[0]
    assert "test-version" in message.answers[0]
