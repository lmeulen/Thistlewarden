"""Process lifetime ownership and local runtime safety checks."""

from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, cast

from evaluator.errors import ConfigurationError


class InstanceLock:
    """Cross-platform non-blocking exclusive lock held for process lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    @classmethod
    def for_database(cls, database_path: Path) -> InstanceLock:
        return cls(database_path.with_name(f"{database_path.name}.instance.lock"))

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch(mode=0o600)
        handle = self.path.open("r+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if handle.read(1) == b"":
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                posix_lock = cast(Any, import_module("fcntl"))
                posix_lock.flock(handle.fileno(), posix_lock.LOCK_EX | posix_lock.LOCK_NB)
            handle.seek(0)
            handle.write(str(os.getpid()).encode("ascii").ljust(32, b" "))
            handle.flush()
            if os.name != "nt":
                self.path.chmod(0o600)
            self._file = handle
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise ConfigurationError(
                "Another evaluator process already owns the database instance lock."
            ) from exc

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        try:
            # Closing descriptor releases both Windows byte-range and POSIX flock locks.
            handle.close()
        finally:
            self._file = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
