"""Tiny cross-process lock + retry-save helper for the council tracker xlsx.

Both poll_declared.py and poll_markets.py modify the same workbook. To avoid
last-write-wins data loss when their save windows overlap, take a coarse file
lock during the load->modify->save cycle.

Cross-platform implementation: O_CREAT|O_EXCL on a sentinel file, retry until
either acquired or timeout. Stale lock is broken if older than STALE_SECONDS.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path(__file__).parent / "council_tracker.lock"
STALE_SECONDS = 120
WAIT_TIMEOUT = 60


@contextmanager
def workbook_lock():
    deadline = time.time() + WAIT_TIMEOUT
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
                if age > STALE_SECONDS:
                    LOCK_PATH.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() > deadline:
                raise TimeoutError(f"could not acquire {LOCK_PATH} within {WAIT_TIMEOUT}s")
            time.sleep(0.5)
    try:
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def save_with_retry(wb, path, attempts: int = 6, delay: float = 2.0) -> None:
    """Save the workbook, retrying on PermissionError (e.g. Excel has it open)."""
    for i in range(attempts):
        try:
            wb.save(path)
            return
        except PermissionError as e:
            if i == attempts - 1:
                raise
            time.sleep(delay)
