"""Runtime singleton lock to prevent multiple polling instances."""

from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RuntimeLock:
    path: str
    _fd: int | None = None

    def acquire(self) -> bool:
        lock_path = Path(self.path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False

        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
