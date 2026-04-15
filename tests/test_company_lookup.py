from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.services import company_lookup


class _FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._body


class _FakeSession:
    def __init__(self, status: int, body: dict):
        self._status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        return _FakeResponse(self._status, self._body)


@pytest.mark.asyncio
async def test_lookup_company_by_inn_success(monkeypatch):
    monkeypatch.setattr(
        company_lookup,
        "get_settings",
        lambda: SimpleNamespace(
            dadata_enabled=True,
            dadata_api_key="token",
            dadata_secret_key="secret",
            dadata_timeout_sec=5.0,
        ),
    )
    monkeypatch.setattr(
        company_lookup.aiohttp,
        "ClientSession",
        lambda **kwargs: _FakeSession(
            200,
            {
                "suggestions": [
                    {
                        "value": 'ООО "ТЕСТ"',
                        "data": {
                            "kpp": "123456789",
                            "ogrn": "1234567890123",
                            "state": {"status": "ACTIVE"},
                            "name": {"short_with_opf": 'ООО "ТЕСТ"'},
                            "address": {"unrestricted_value": "г. Санкт-Петербург, Невский проспект, д. 1"},
                        },
                    }
                ]
            },
        ),
    )

    company = await company_lookup.lookup_company_by_inn("7806341520")
    assert company is not None
    assert company.name == 'ООО "ТЕСТ"'
    assert company.kpp == "123456789"
    assert company.address.startswith("г. Санкт-Петербург")


@pytest.mark.asyncio
async def test_lookup_company_by_inn_empty_suggestions(monkeypatch):
    monkeypatch.setattr(
        company_lookup,
        "get_settings",
        lambda: SimpleNamespace(
            dadata_enabled=True,
            dadata_api_key="token",
            dadata_secret_key="",
            dadata_timeout_sec=5.0,
        ),
    )
    monkeypatch.setattr(
        company_lookup.aiohttp,
        "ClientSession",
        lambda **kwargs: _FakeSession(200, {"suggestions": []}),
    )

    company = await company_lookup.lookup_company_by_inn("7806341520")
    assert company is None


@pytest.mark.asyncio
async def test_lookup_company_by_inn_http_error(monkeypatch):
    monkeypatch.setattr(
        company_lookup,
        "get_settings",
        lambda: SimpleNamespace(
            dadata_enabled=True,
            dadata_api_key="token",
            dadata_secret_key="",
            dadata_timeout_sec=5.0,
        ),
    )
    monkeypatch.setattr(
        company_lookup.aiohttp,
        "ClientSession",
        lambda **kwargs: _FakeSession(500, {"message": "internal error"}),
    )

    company = await company_lookup.lookup_company_by_inn("7806341520")
    assert company is None


@pytest.mark.asyncio
async def test_lookup_company_by_inn_disabled(monkeypatch):
    monkeypatch.setattr(
        company_lookup,
        "get_settings",
        lambda: SimpleNamespace(
            dadata_enabled=False,
            dadata_api_key="",
            dadata_secret_key="",
            dadata_timeout_sec=5.0,
        ),
    )

    company = await company_lookup.lookup_company_by_inn("7806341520")
    assert company is None
