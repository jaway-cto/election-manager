"""
notify.py — Alert sink. Telegram if configured, otherwise stdout.

Configure with:
    setx TELEGRAM_BOT_TOKEN "1234:ABC..."   # one-time
    setx TELEGRAM_CHAT_ID   "123456789"

Two channel concepts:
    - "actionable" — investor needs to look NOW (signal, large edge, fills)
    - "fyi"        — informational (heartbeats, sweep summaries)

Usage:
    from notify import alert, fyi
    alert("BTC $75k YES dropped to 41c (fair 38%) — edge 5pp, spread 1.5pp")
    fyi("scanner heartbeat: 3 markets flagged, 0 actionable")
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

LOG_PATH = Path(os.environ.get("ODDS_LOG_PATH", r"C:\Dev\odds\logs\notify.log"))
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage" if TG_TOKEN else ""


def _log(level: str, channel: str, msg: str) -> None:
    line = f"{dt.datetime.now(dt.timezone.utc).isoformat()}  [{level:<7}] [{channel:<10}] {msg}\n"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    try:
        sys.stdout.write(line)
    except UnicodeEncodeError:
        sys.stdout.write(line.encode("ascii", "replace").decode("ascii"))
    sys.stdout.flush()


def _send_telegram(text: str, chat_id: str | None = None,
                   parse_mode: str = "Markdown") -> bool:
    if not TG_API:
        return False
    chat = chat_id or TG_CHAT
    if not chat:
        return False
    try:
        body = urllib.parse.urlencode({
            "chat_id": chat, "text": text[:4090],
            "parse_mode": parse_mode, "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(TG_API, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        sys.stderr.write(f"telegram send failed: {e}\n")
        return False


def alert(msg: str, *, prefix: str = "ALERT") -> None:
    """Actionable signal — high priority. Goes to TG_CHAT_ID."""
    text = f"❗ *{prefix}* — {msg}"
    _log("ALERT", "actionable", msg)
    _send_telegram(text)


def fyi(msg: str) -> None:
    """Informational — heartbeats, summaries. Same chat by default but distinguishable."""
    text = f"ℹ️ {msg}"
    _log("FYI", "fyi", msg)
    _send_telegram(text)


def event(category: str, payload: dict) -> None:
    """Structured event (for grep + replay)."""
    line = json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "category": category, **payload})
    _log("EVENT", category, line)


if __name__ == "__main__":
    # CLI: python notify.py "your message"
    msg = " ".join(sys.argv[1:]) or "test alert from odds notify.py"
    alert(msg)
    print("Sent.")
