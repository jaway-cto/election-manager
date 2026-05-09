r"""
killswitch.py — Cheap, fast halt mechanism for any auto-trading process.

Three ways to flip the switch:
    1. Env var: ODDS_TRADING_HALT=1
    2. File flag exists: C:\Dev\odds\HALT (or $ODDS_HALT_FILE)
    3. SQLite kill flag (set via Telegram /halt command, future)

Every trading loop should call:
    if killswitch.tripped():
        sys.exit(0)
    or
    killswitch.assert_armed()  # raises Halted if tripped

Status is cached for 1 second to avoid file-system spam.
"""
from __future__ import annotations
import os
import time
from pathlib import Path

HALT_FILE = Path(os.environ.get("ODDS_HALT_FILE", r"C:\Dev\odds\HALT"))
_LAST_CHECK = 0.0
_LAST_VAL = False
_REASON = ""


class Halted(RuntimeError):
    pass


def tripped(force_check: bool = False) -> bool:
    global _LAST_CHECK, _LAST_VAL, _REASON
    now = time.time()
    if not force_check and now - _LAST_CHECK < 1.0:
        return _LAST_VAL
    _LAST_CHECK = now
    if os.environ.get("ODDS_TRADING_HALT") in ("1", "true", "yes", "on"):
        _LAST_VAL = True
        _REASON = "ODDS_TRADING_HALT env var set"
        return True
    if HALT_FILE.exists():
        try:
            content = HALT_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            content = ""
        _LAST_VAL = True
        _REASON = f"halt file present: {HALT_FILE} ({content[:80] or 'no message'})"
        return True
    _LAST_VAL = False
    _REASON = ""
    return False


def assert_armed() -> None:
    """Raise Halted if tripped. Use to bail early in a loop."""
    if tripped():
        raise Halted(_REASON or "killswitch tripped")


def reason() -> str:
    return _REASON


def trip(message: str = "manual halt") -> None:
    """Trip the switch by writing the halt file."""
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HALT_FILE.write_text(message, encoding="utf-8")


def reset() -> None:
    """Re-arm: remove halt file. Env var must be unset separately."""
    if HALT_FILE.exists():
        HALT_FILE.unlink()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "trip":
        trip(" ".join(sys.argv[2:]) or "manual halt")
        print(f"halt file written at {HALT_FILE}")
    elif len(sys.argv) > 1 and sys.argv[1] == "reset":
        reset()
        print("halt file removed (env var must be unset separately)")
    else:
        if tripped(force_check=True):
            print(f"TRIPPED: {reason()}")
            sys.exit(1)
        print("ARMED — auto-trading allowed")
