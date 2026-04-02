"""Shared fixtures for the test suite."""

from __future__ import annotations

import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure the project root is on sys.path so `bot.*` imports resolve
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("BOT_TOKEN", "test:fake-token-for-tests")

from bot.database.models import Base


@pytest_asyncio.fixture
async def db_session():
    """In-memory SQLite async session that creates all tables automatically."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
