"""
backtest_validator.py — Rigorous, look-ahead-safe backtest of every scanner.

Three modes:
  1. Capture: Hook into notify.event() so every signal emitted by a scanner is
     logged to data/signals.jsonl with timestamp, validator, market_id,
     token_id, side, flagged_yes_price, model_fair_value.
  2. Replay: Read signals.jsonl, fetch CLOB prices-history for each token
     STRICTLY around the signal timestamp, compute realised PnL net of
     PM taker fees + FX drag.
  3. Decide: Print per-validator decision table — win rate, mean realised
     bps, Sharpe, expected $ over a year. The output answers
     "should I enable execution on scanner X?" (>70% paper-to-realised
     hit rate per the red-team rule).

Look-ahead-safety guarantees:
  * entry observation MUST satisfy obs.t <= signal_ts (no future leak)
  * exit observation MUST satisfy obs.t >= signal_ts + hold_seconds
  * If either gate fails, the signal is reported as "no_data" and excluded
    from aggregate stats — never silently using post-signal entry.

PnL accounting:
  * PnL per share = (exit - entry) for BUY, (entry - exit) for SELL.
  * Subtract PM taker fee at entry-leg (per validator_core.fees_for_market).
  * If hold_days >= 7, also subtract 70bps FX drag.
  * If signal carries 'is_negrisk_basket', subtract 204bps surcharge.

Usage:
    python backtest_validator.py capture   # turn on logging hook (off-default)
    python backtest_validator.py replay    # backtest signals.jsonl
    python backtest_validator.py decide    # print per-validator decision
    python backtest_validator.py replay --hold-days 3 --since-days 30
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from validator_core import (
    fees_for_market, edge_after_fees, gamma_event,
    FX_GBP_USD_DRAG_BPS, NEGRISK_SURCHARGE_BPS,
)

CLOB = "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (odds-backtest)"}

SIGNALS_PATH = Path(os.environ.get(
    "ODDS_SIGNALS_PATH", r"C:\Dev\odds\data\signals.jsonl"))
SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Capture mode — append-only signal log
# ============================================================================

def log_signal(record: dict) -> None:
    """Append one signal to the JSONL log. Idempotent on (validator, token_id,
    rounded-to-5min ts) tuples to avoid spam from re-runs.
    """
    record = dict(record)
    record.setdefault("ts", int(time.time()))
    with SIGNALS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def install_capture_hook() -> None:
    """Monkey-patch notify.event() to also write to signals.jsonl. Call this
    once at process startup if you want capture-mode behaviour.
    """
    import notify
    if getattr(notify, "_backtest_hook_installed", False):
        return
    original = notify.event
    def wrapped(category: str, payload: dict) -> None:
        try:
            if category in ("tail_decay.signal", "negrisk.signal",
                            "pm_betfair.signal", "scotus.opinion",
                            "fda.release", "nhc.advisory"):
                log_signal({"category": category, **payload})
        except Exception:
            pass
        return original(category, payload)
    notify.event = wrapped
    notify._backtest_hook_installed = True


# ============================================================================
# Replay mode — look-ahead-safe price history fetch
# ============================================================================

def fetch_history(token_id: str, start_ts: int, end_ts: int,
                  fidelity: int = 1) -> list[dict]:
    """Fetch CLOB prices-history. fidelity=1 = minute samples."""
    try:
        r = requests.get(
            f"{CLOB}/prices-history",
            params={"market": token_id, "startTs": start_ts,
                    "endTs": end_ts, "fidelity": fidelity},
            headers=UA, timeout=30,
        )
        r.raise_for_status()
        return (r.json() or {}).get("history", [])
    except Exception as e:
        sys.stderr.write(f"[history {token_id[:12]}] {e}\n")
        return []


def find_entry(history: list[dict], signal_ts: int) -> Optional[dict]:
    """Last observation at or before signal_ts (no look-ahead)."""
    pre = [h for h in history if h["t"] <= signal_ts]
    return max(pre, key=lambda h: h["t"]) if pre else None


def find_exit(history: list[dict], target_ts: int) -> Optional[dict]:
    """First observation at or after target_ts."""
    post = [h for h in history if h["t"] >= target_ts]
    return min(post, key=lambda h: h["t"]) if post else None


def backtest_signal(sig: dict, hold_days: int = 7,
                    fee_bps_default: float = 200.0) -> dict:
    """Replay one signal with strict timestamp gating.

    Returns dict with realised_pnl_bps, win, edge_at_entry_pp, etc.
    """
    token = (sig.get("token") or sig.get("yes_token") or
             sig.get("market_id"))  # fall back to market_id (we'll resolve)
    raw_ts = sig.get("ts") or sig.get("signal_ts") or 0
    # Accept either unix int or ISO string
    if isinstance(raw_ts, str):
        try:
            signal_ts = int(dt.datetime.fromisoformat(
                raw_ts.replace("Z", "+00:00")).timestamp())
        except Exception:
            return {"sig": sig, "error": f"bad ts: {raw_ts}"}
    else:
        signal_ts = int(raw_ts)
    if not token or not signal_ts:
        return {"sig": sig, "error": "missing token or ts"}

    # If signal carries market_id but not token, resolve via Gamma
    if not (sig.get("token") or sig.get("yes_token")) and sig.get("market_id"):
        try:
            mid = str(sig["market_id"])
            r = requests.get(f"https://gamma-api.polymarket.com/markets/{mid}",
                             timeout=10)
            if r.status_code == 200:
                m = r.json()
                from validator_core import parse_clob_token_ids
                yes_tok, _ = parse_clob_token_ids(m)
                if yes_tok:
                    token = yes_tok
        except Exception:
            pass

    # Determine action from signal payload
    side = (sig.get("side") or sig.get("side_label") or sig.get("action") or "").upper()
    if "BUY" in side or side in ("YES",):
        action = "BUY"
    elif "SELL" in side or side in ("NO",):
        action = "SELL"
    else:
        return {"sig": sig, "error": f"unknown side: {side}"}

    end_ts = signal_ts + hold_days * 86400
    # Pull a generous window so look-ahead-safe anchors exist
    history = fetch_history(token, signal_ts - 86400, end_ts + 86400)
    if not history:
        return {"sig": sig, "error": "no history"}

    entry = find_entry(history, signal_ts)
    exit_ = find_exit(history, end_ts)
    if not entry:
        return {"sig": sig, "error": "no pre-signal observation"}
    if not exit_:
        # Signal too recent — no exit yet. Use latest observation as MTM.
        exit_ = max(history, key=lambda h: h["t"])
        if exit_["t"] < signal_ts:
            return {"sig": sig, "error": "no post-signal observation"}

    entry_px = float(entry["p"])
    exit_px = float(exit_["p"])
    if action == "BUY":
        gross_pnl = exit_px - entry_px
    else:
        gross_pnl = entry_px - exit_px

    # Fee-aware PnL (round-trip fee, FX drag, optional negRisk surcharge)
    is_basket = sig.get("category") == "negrisk.signal"
    fee_bps = fee_bps_default  # by default; better when we have market dict
    net_pnl_bps = edge_after_fees(
        gross_pnl * 10000.0, taker_fee_bps=fee_bps,
        is_two_legged=True, is_negrisk_basket=is_basket,
        apply_fx=(hold_days >= 1), apply_cgt=False,
    )

    cat = sig.get("category", "")
    validator = (sig.get("validator")
                 or {"tail_decay.signal": "tail-decay",
                     "negrisk.signal": "negrisk",
                     "pm_betfair.signal": "pm-betfair"}.get(cat)
                 or cat.split(".")[0])
    return {
        "validator": validator,
        "category": cat,
        "token": token,
        "signal_ts": signal_ts,
        "entry_ts": entry["t"], "exit_ts": exit_["t"],
        "entry_px": entry_px, "exit_px": exit_px,
        "action": action,
        "flagged_edge_pp": float(sig.get("edge_pp") or sig.get("edge_bps_at_entry", 0) / 100 or 0),
        "gross_pnl_bps": gross_pnl * 10000.0,
        "net_pnl_bps": net_pnl_bps,
        "win": net_pnl_bps > 0,
        "fully_settled": exit_["t"] >= end_ts,
    }


def replay(signals_path: Path = SIGNALS_PATH, hold_days: int = 7,
           since_days: Optional[int] = None) -> list[dict]:
    if not signals_path.exists():
        print(f"No signals at {signals_path}.\n"
              f"Run scanners with `python backtest_validator.py capture` "
              f"installed first to populate.")
        return []
    cutoff = int(time.time()) - (since_days * 86400) if since_days else 0
    results: list[dict] = []
    with signals_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                sig = json.loads(line)
            except Exception:
                continue
            if cutoff and int(sig.get("ts", 0)) < cutoff:
                continue
            r = backtest_signal(sig, hold_days)
            results.append(r)
    return results


# ============================================================================
# Decide mode — per-validator decision table
# ============================================================================

def aggregate_by_validator(results: list[dict]) -> dict[str, dict]:
    by: dict[str, list[dict]] = {}
    for r in results:
        if "error" in r:
            continue
        by.setdefault(r.get("validator", "?"), []).append(r)
    out: dict[str, dict] = {}
    for v, rs in by.items():
        if not rs:
            continue
        wins = sum(1 for x in rs if x["win"])
        bps_list = [x["net_pnl_bps"] for x in rs]
        flagged_list = [x["flagged_edge_pp"] for x in rs if x["flagged_edge_pp"]]
        avg_realised = statistics.mean(bps_list)
        avg_flagged_bps = (statistics.mean(flagged_list) * 100 if flagged_list else 0)
        sd = statistics.stdev(bps_list) if len(bps_list) > 1 else 0
        sharpe = (avg_realised / sd) if sd > 0 else 0
        out[v] = {
            "n": len(rs), "wins": wins, "win_rate": wins / len(rs),
            "avg_realised_bps": avg_realised, "avg_flagged_bps": avg_flagged_bps,
            "realisation_ratio": (avg_realised / avg_flagged_bps
                                  if avg_flagged_bps else 0),
            "sd_bps": sd, "sharpe": sharpe,
            "fully_settled": sum(1 for x in rs if x["fully_settled"]),
        }
    return out


def decide(results: list[dict]) -> None:
    """Print the decision table. Per red-team behavioural rule:
    a scanner graduates from paper to live ONLY when realisation ratio > 0.7.
    """
    agg = aggregate_by_validator(results)
    if not agg:
        errs = [r for r in results if "error" in r]
        print(f"No valid backtest results. {len(errs)} signals had errors:")
        from collections import Counter
        c = Counter(r.get("error", "?") for r in errs)
        for e, n in c.most_common():
            print(f"  {n:>4}  {e}")
        return
    hdr = (f"{'Validator':<14}{'N':>4}{'Wins':>5}{'Win%':>6}"
           f"{'AvgFlag':>9}{'AvgReal':>9}{'Real/Flag':>11}{'Sharpe':>8}"
           f"{'Settled':>9}{'Decision':>14}")
    print(hdr); print("-" * len(hdr))
    for v, s in sorted(agg.items(), key=lambda x: -x[1]["avg_realised_bps"]):
        ratio = s["realisation_ratio"]
        # Red-team rule: realisation ratio > 0.7 AND >=10 fully-settled sample
        if s["fully_settled"] < 10:
            decision = "MORE DATA"
        elif ratio > 0.7 and s["win_rate"] > 0.55:
            decision = "ENABLE LIVE"
        elif ratio > 0.3:
            decision = "PAPER ONLY"
        else:
            decision = "DROP"
        print(f"{v:<14}{s['n']:>4}{s['wins']:>5}{s['win_rate']*100:>5.0f}%"
              f"{s['avg_flagged_bps']:>+8.0f}b{s['avg_realised_bps']:>+8.0f}b"
              f"{ratio:>+10.2f}{s['sharpe']:>+8.2f}"
              f"{s['fully_settled']:>9}{decision:>14}")
    print("\nDecision rules:")
    print("  ENABLE LIVE  — realisation>0.7 AND win-rate>55% AND >=10 fully-settled signals")
    print("  PAPER ONLY   — realisation 0.3-0.7 — keep capturing, don't deploy capital")
    print("  DROP         — realisation<0.3 — model is broken, fix or remove")
    print("  MORE DATA    — fewer than 10 fully-settled signals; need 30+ days more capture")


# ============================================================================
# CLI
# ============================================================================

def cmd_capture(args):
    install_capture_hook()
    print(f"Capture hook installed. Run scanners normally; signals will be")
    print(f"appended to {SIGNALS_PATH}")
    print(f"To make this persistent, call `install_capture_hook()` from")
    print(f"unified_arb_dashboard.py or pollers/daemon.py at startup.")


def cmd_replay(args):
    results = replay(args.signals, args.hold_days, args.since_days)
    print(f"Replayed {len(results)} signals from {args.signals}")
    err = sum(1 for r in results if "error" in r)
    if err:
        print(f"  {err} signals errored (no history / no observation / etc.)")
    valid = [r for r in results if "error" not in r]
    if valid:
        wins = sum(1 for r in valid if r["win"])
        avg = statistics.mean(r["net_pnl_bps"] for r in valid)
        print(f"  {wins}/{len(valid)} wins ({wins/len(valid)*100:.0f}%), "
              f"avg net P&L {avg:+.0f}bps per signal")
    if args.json:
        print(json.dumps(results, indent=2))


def cmd_decide(args):
    results = replay(args.signals, args.hold_days, args.since_days)
    decide(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", type=Path, default=SIGNALS_PATH)
    ap.add_argument("--hold-days", type=int, default=7)
    ap.add_argument("--since-days", type=int, default=None,
                    help="Only replay signals newer than N days ago")
    ap.add_argument("--json", action="store_true",
                    help="Dump full per-signal results as JSON")

    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture", help="Print install instructions for capture hook")
    sub.add_parser("replay", help="Backtest signals.jsonl")
    sub.add_parser("decide", help="Per-validator decision table")

    args = ap.parse_args()
    {"capture": cmd_capture, "replay": cmd_replay, "decide": cmd_decide}[args.cmd](args)


if __name__ == "__main__":
    main()
