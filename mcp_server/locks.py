"""Operating-system file locks (Problem 5, Stage 4).

The lock is held on an open file descriptor (msvcrt on Windows, flock elsewhere), so the
operating system releases it automatically if the holding process crashes: no stale locks.

  trail_lock   serialises writers of output/audit_trail.json
  run_lock     held for the whole of an agent run; reset and snapshot refuse while it is held
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

RUN_LOCK_NAME = ".run.lock"


class LockTimeout(TimeoutError):
    pass


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def file_lock(lock_path: Path, timeout: float = 15.0):
    """Exclusive lock on lock_path. timeout=0 means: fail at once if someone else holds it."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        # Never write to the lock file: Windows byte-range locks are mandatory, so writing a byte that
        # another process has locked fails with "Permission denied" (found by the concurrency test on
        # 2026-10-09). Locking a byte beyond end-of-file is allowed, so the file can stay empty.
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockTimeout(f"{lock_path.name} is held by another process")
            time.sleep(0.01)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def run_lock_path(active_dir: Path) -> Path:
    return Path(active_dir) / RUN_LOCK_NAME


def run_active(active_dir: Path) -> bool:
    """True if an agent run currently holds the run lock (checked without waiting).
    Read-only callers must not create files: a run always creates the lock file before holding it,
    so a missing file means no run is active."""
    if not run_lock_path(active_dir).exists():
        return False
    try:
        with file_lock(run_lock_path(active_dir), timeout=0):
            return False
    except LockTimeout:
        return True
