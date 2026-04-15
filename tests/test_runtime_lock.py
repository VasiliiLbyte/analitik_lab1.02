from __future__ import annotations

from bot.services.runtime_lock import RuntimeLock


def test_runtime_lock_blocks_second_acquire(tmp_path):
    lock_path = tmp_path / "bot.lock"
    first = RuntimeLock(str(lock_path))
    second = RuntimeLock(str(lock_path))

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()


def test_runtime_lock_release_allows_reacquire(tmp_path):
    lock_path = tmp_path / "bot.lock"
    first = RuntimeLock(str(lock_path))
    second = RuntimeLock(str(lock_path))

    assert first.acquire() is True
    first.release()
    assert second.acquire() is True
    second.release()
