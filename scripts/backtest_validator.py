"""
backtest_validator.py — Validate fair-value models by checking historical
PM price moves after a flagged edge.

Uses CLOB /prices-history endpoint (free, unauth).

Workflow:
  1. Provide a market token_id and a "signal" timestamp (when our model would
     have flagged a BUY YES or SELL YES).
  2. Fetch prices over the holding window.
  3. Compute realised P&L vs flagged edge.
  4. Aggregate across multiple signals to estimate model quality.

Usage (single market):
    python backtest_validator.py --token 0x... --signal-ts 1715000000 \\
        --action "BUY YES" --hold-days 7

Batch mode (operate on a JSONL file of historical signals):
    python backtest_validator.py --signals signals.jsonl
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from typing import Optional

import requests

CLOB = "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (backtest)"}


def fetch_history(token_id: str, start_ts: int, end_ts: int,
                  fidelity: int = 60) -> list[dict]:
    """Fetch CLOB prices-history for a token over [start_ts, end_ts].

    Args:
        token_id: CLOB token ID
        start_ts/end_ts: unix seconds
        fidelity: granularity in minutes (60 = hourly samples)
    Returns: list of {"t": unix_ts, "p": price}
    """
    params = {
        "market": token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": fidelity,
    }
    r = requests.get(f"{CLOB}/prices-history", params=params,
                     headers=UA, timeout=30)
    r.raise_for_status()
    return (r.json() or {}).get("history", [])


def backtest_signal(token_id: str, signal_ts: int, action: str,
                    flagged_yes_price: float, model_fair: float,
                    hold_days: int = 7) -> dict:
    """Backtest one signal.

    Returns: {
        "entry_price": ...,        # closest observation to signal_ts
        "exit_price": ...,         # observation hold_days later
        "realised_move": ...,      # exit - entry (in YES probability units)
        "model_fair": ...,
        "edge_at_signal": fair - entry,
        "pnl_bps": ...,            # P&L per share in bps
        "win": True/False,
    }
    """
    end_ts = signal_ts + hold_days * 86400
    hist = fetch_history(token_id, signal_ts - 3600, end_ts + 3600)
    if not hist:
        return {"error": "no history", "token": token_id}

    # Find entry near signal_ts
    entry = min(hist, key=lambda h: abs(h["t"] - signal_ts))
    exit_ = min(hist, key=lambda h: abs(h["t"] - end_ts))

    entry_price = float(entry["p"])
    exit_price = float(exit_["p"])
    realised = exit_price - entry_price

    # P&L: BUY YES profits when YES price rises; SELL YES profits when it falls.
    if action.upper().startswith("BUY"):
        pnl = (exit_price - entry_price)
    else:  # SELL
        pnl = (entry_price - exit_price)
    pnl_bps = pnl * 10000.0

    return {
        "token": token_id,
        "signal_ts": signal_ts,
        "entry_ts": entry["t"], "exit_ts": exit_["t"],
        "entry_price": entry_price,
        "exit_price": exit_price,
        "realised_move": realised,
        "model_fair": model_fair,
        "edge_at_signal_pp": (model_fair - entry_price) * 100,
        "pnl_bps": pnl_bps,
        "win": pnl > 0,
        "action": action,
    }


def aggregate(results: list[dict]) -> dict:
    valid = [r for r in results if "error" not in r]
    if not valid:
        return {"n": 0}
    wins = sum(1 for r in valid if r["win"])
    avg_pnl = sum(r["pnl_bps"] for r in valid) / len(valid)
    return {
        "n": len(valid),
        "wins": wins,
        "win_rate": wins / len(valid),
        "avg_pnl_bps": avg_pnl,
        "total_pnl_bps": sum(r["pnl_bps"] for r in valid),
        "errors": len(results) - len(valid),
    }


def cli_single(args):
    res = backtest_signal(
        token_id=args.token,
        signal_ts=args.signal_ts,
        action=args.action,
        flagged_yes_price=args.flagged_yes_price,
        model_fair=args.model_fair,
        hold_days=args.hold_days,
    )
    print(json.dumps(res, indent=2))


def cli_batch(args):
    results = []
    with open(args.signals) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sig = json.loads(line)
            res = backtest_signal(
                token_id=sig["token"], signal_ts=int(sig["signal_ts"]),
                action=sig["action"],
                flagged_yes_price=float(sig.get("entry_price", 0)),
                model_fair=float(sig.get("model_fair", 0)),
                hold_days=args.hold_days,
            )
            results.append(res)
            print(json.dumps(res))
    summary = aggregate(results)
    print(f"\n=== Backtest summary ===", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", help="CLOB token id")
    ap.add_argument("--signal-ts", type=int, help="Unix timestamp of signal")
    ap.add_argument("--action", default="BUY YES", help='"BUY YES" or "SELL YES"')
    ap.add_argument("--flagged-yes-price", type=float, default=0.0)
    ap.add_argument("--model-fair", type=float, default=0.0)
    ap.add_argument("--hold-days", type=int, default=7)
    ap.add_argument("--signals", help="JSONL file of signals for batch backtesting")
    args = ap.parse_args()

    if args.signals:
        cli_batch(args)
    elif args.token and args.signal_ts:
        cli_single(args)
    else:
        ap.error("provide either --token + --signal-ts, or --signals <file>")


if __name__ == "__main__":
    main()
