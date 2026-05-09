"""
verify_setup.py — One-shot health check.

Tests:
  * Python deps (requests, scipy, numpy, openpyxl, telegram, optional web3)
  * Telegram bot reachable
  * Polymarket Gamma + CLOB endpoints reachable
  * Trade-mode env vars present (or note they're absent)
  * Killswitch armed
  * SQLite DBs writable
  * Recent signal capture working

Run after each setup step. Prints a checklist with pass/fail.

Usage:
    python verify_setup.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path


def status(label: str, ok: bool, detail: str = "") -> None:
    mark = "[ OK ]" if ok else "[FAIL]"
    msg = f"  {mark}  {label}"
    if detail:
        msg += f"   {detail}"
    print(msg)


def warn(label: str, detail: str = "") -> None:
    print(f"  [WARN]  {label}   {detail}")


def section(name: str) -> None:
    print(f"\n=== {name} ===")


def check_python_deps() -> bool:
    section("Python dependencies")
    required = [
        ("requests", True),
        ("scipy", True),
        ("numpy", True),
        ("openpyxl", True),
        ("telegram", False),         # Telegram alerts (optional)
        ("pdfminer.high_level", False),  # SCOTUS PDF parsing (optional)
        ("web3", False),             # Live trading (optional)
        ("py_clob_client", False),   # Live trading (optional)
    ]
    all_required_ok = True
    for mod, required in required:
        try:
            __import__(mod)
            status(mod, True, "" if required else "(optional)")
        except ImportError:
            if required:
                status(mod, False, "REQUIRED — pip install <pkg>")
                all_required_ok = False
            else:
                warn(mod, "optional — system works without; pip install if needed")
    return all_required_ok


def check_env_vars() -> dict[str, bool]:
    section("Environment variables")
    vars_ = {
        "TELEGRAM_BOT_TOKEN": "Telegram alerts (optional)",
        "TELEGRAM_CHAT_ID": "Telegram alerts (optional)",
        "THE_ODDS_API_KEY": "Sports validator (optional, free tier)",
        "FRED_API_KEY": "WTI fallback fetch (optional)",
        "PM_PRIVATE_KEY": "Polymarket trade mode (optional)",
        "PM_API_KEY": "Polymarket trade mode (optional)",
        "PM_API_SECRET": "Polymarket trade mode (optional)",
        "PM_API_PASSPHRASE": "Polymarket trade mode (optional)",
        "PM_PROXY_ADDRESS": "Polymarket trade mode (optional)",
        "PM_TRADING_ENABLED": "Polymarket trade mode (must be '1' to trade)",
        "BETFAIR_APP_KEY": "Betfair cross-venue (optional)",
        "BETFAIR_USERNAME": "Betfair cross-venue (optional)",
        "BETFAIR_PASSWORD": "Betfair cross-venue (optional)",
    }
    out = {}
    for k, desc in vars_.items():
        present = bool(os.environ.get(k))
        out[k] = present
        if present:
            v = os.environ[k]
            masked = (v[:4] + "..." + v[-4:]) if len(v) > 12 else "***"
            status(k, True, f"{masked}  ({desc})")
        else:
            warn(k, f"unset  ({desc})")
    return out


def check_telegram(env: dict[str, bool]) -> None:
    section("Telegram alerts")
    if not (env["TELEGRAM_BOT_TOKEN"] and env["TELEGRAM_CHAT_ID"]):
        warn("Telegram", "skipped — env vars not set")
        return
    import urllib.request, urllib.parse, json
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    try:
        body = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": "[READY] odds setup verify_setup.py — Telegram OK",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status == 200
        status("Telegram sendMessage", ok)
    except Exception as e:
        status("Telegram sendMessage", False, str(e))


def check_polymarket() -> None:
    section("Polymarket connectivity (read)")
    try:
        from validator_core import gamma_event, get_quote, parse_clob_token_ids
    except ImportError as e:
        status("validator_core import", False, str(e))
        return
    ev = gamma_event("what-price-will-bitcoin-hit-in-may-2026")
    status("Gamma /events", bool(ev),
           f"{len(ev.get('markets', []))} markets" if ev else "no response")
    if ev and ev.get("markets"):
        m = ev["markets"][0]
        yes_tok, _ = parse_clob_token_ids(m)
        if yes_tok:
            q = get_quote(yes_tok)
            status("CLOB /book", bool(q.has_book),
                   f"bid={q.bid} ask={q.ask}" if q.has_book else "")


def check_killswitch() -> None:
    section("Killswitch")
    try:
        import killswitch
    except ImportError as e:
        status("killswitch import", False, str(e))
        return
    tripped = killswitch.tripped(force_check=True)
    status("Killswitch", not tripped,
           "TRIPPED — would block any live trade"
           if tripped else "armed (auto-trading allowed)")


def check_databases() -> None:
    section("SQLite databases")
    paths = [
        Path(r"C:\Dev\odds\data\positions.sqlite"),
        Path(r"C:\Dev\odds\data\pollers.sqlite"),
        Path(r"C:\Dev\odds\data\signals.jsonl"),
    ]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Just verify writability
            test_path = p.parent / ".write_test"
            test_path.write_text("ok")
            test_path.unlink()
            present = p.exists()
            status(f"{p.name}", True,
                   f"exists ({p.stat().st_size:,} bytes)"
                   if present else "will be created on first use")
        except Exception as e:
            status(f"{p.name}", False, str(e))


def check_signal_capture() -> None:
    section("Signal capture")
    sig = Path(r"C:\Dev\odds\data\signals.jsonl")
    if not sig.exists():
        warn("signals.jsonl", "no signals captured yet")
        return
    n = sum(1 for _ in sig.open(encoding="utf-8"))
    status("signals.jsonl", n > 0, f"{n} signals captured")


def summary(env: dict[str, bool], deps_ok: bool) -> None:
    section("Summary")
    paper_ready = deps_ok
    sports_ready = paper_ready and env["THE_ODDS_API_KEY"]
    telegram_ready = env["TELEGRAM_BOT_TOKEN"] and env["TELEGRAM_CHAT_ID"]
    trade_ready = (paper_ready
                   and all(env[k] for k in (
                       "PM_PRIVATE_KEY", "PM_API_KEY", "PM_API_SECRET",
                       "PM_API_PASSPHRASE", "PM_PROXY_ADDRESS"))
                   and os.environ.get("PM_TRADING_ENABLED") == "1")
    bf_ready = all(env[k] for k in
                   ("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD"))

    bars = [
        ("Read-only paper trial", paper_ready,
         "  python unified_arb_dashboard.py"),
        ("Telegram alerts", telegram_ready,
         "  set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (Step 1)"),
        ("Sports validator", sports_ready,
         "  set THE_ODDS_API_KEY (Step 3)"),
        ("Polymarket auto-trading", trade_ready,
         "  set PM_PRIVATE_KEY+API+PROXY+TRADING_ENABLED=1 (Step 4)"),
        ("Betfair cross-venue", bf_ready,
         "  set BETFAIR_APP_KEY+USERNAME+PASSWORD (Step 5)"),
    ]
    for label, ok, fix in bars:
        status(label, ok, "READY" if ok else f"to enable: {fix}")

    print()
    if paper_ready:
        print("[READY] Read-only paper trial is READY. Run:")
        print("     cd C:\\Dev\\odds\\scripts")
        print("     python unified_arb_dashboard.py")
        print("     python -m pollers.daemon")
    else:
        print("[NOT READY] Required deps missing. Install: pip install requests scipy numpy openpyxl")


def main() -> None:
    print("=" * 60)
    print("odds — setup verifier")
    print("=" * 60)
    deps_ok = check_python_deps()
    env = check_env_vars()
    check_telegram(env)
    check_polymarket()
    check_killswitch()
    check_databases()
    check_signal_capture()
    summary(env, deps_ok)


if __name__ == "__main__":
    main()
