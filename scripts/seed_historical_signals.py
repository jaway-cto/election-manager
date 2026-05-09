"""
Seed signals.jsonl with historical Polymarket markets that have already
resolved, so the backtest harness has something to chew on right now
(rather than waiting 7 days for live captured signals to settle).

Strategy:
  * Pull a window of past markets from Gamma where closed=true.
  * For each, find a moment N days before resolution where the YES ask
    was within the tail-decay scanner's "would have flagged" range
    (0.92-0.99 for tail-decay sweep; 0.92-1.00 for confidence).
  * Synthesize a signal at that moment with the would-have-been ask price.
  * Backtest will then compare entry_px to actual resolved value (1.0 if
    YES won, 0.0 if NO won).

Usage:
    python seed_historical_signals.py --closed-since-days 30 --limit 50
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

from validator_core import parse_clob_token_ids, GAMMA, UA
from backtest_validator import fetch_history, SIGNALS_PATH


def fetch_closed_markets(since_days: int = 30, limit: int = 100) -> list[dict]:
    """Pull markets that closed within last `since_days`."""
    end_min = (dt.datetime.now(dt.timezone.utc)
               - dt.timedelta(days=since_days)).isoformat()
    out: list[dict] = []
    for offset in range(0, 1000, 100):
        try:
            r = requests.get(
                f"{GAMMA}/markets",
                params={"closed": "true", "limit": 100, "offset": offset,
                        "end_date_min": end_min, "order": "endDate",
                        "ascending": "false"},
                headers=UA, timeout=20,
            )
            r.raise_for_status()
            data = r.json() or []
        except Exception as e:
            sys.stderr.write(f"[gamma] fetch failed: {e}\n")
            break
        if not data:
            break
        out.extend(data)
        if len(out) >= limit:
            break
    return out[:limit]


def synthesize_tail_decay_signal(market: dict, days_before_close: int = 3
                                 ) -> dict | None:
    """For a closed market: find the minute-level price ~3 days before close.
    If that price was 0.92-0.99 on YES, treat it as a would-have-been signal."""
    yes_tok, no_tok = parse_clob_token_ids(market)
    if not yes_tok:
        return None
    end_iso = market.get("endDate") or ""
    try:
        end_dt = dt.datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    signal_ts = int((end_dt - dt.timedelta(days=days_before_close)).timestamp())
    # Pull 4hr around that moment
    hist = fetch_history(yes_tok, signal_ts - 7200, signal_ts + 7200, fidelity=1)
    if not hist:
        return None
    pre = [h for h in hist if h["t"] <= signal_ts]
    if not pre:
        return None
    obs = max(pre, key=lambda h: h["t"])
    yes_ask = float(obs["p"])
    # tail-decay would have flagged this if 0.85 <= ask <= 0.99 (broadened
    # from production 0.92 to capture more historical samples).
    no_ask = 1 - yes_ask  # approximation; real NO book may differ
    if 0.85 <= yes_ask <= 0.99:
        side, token = "YES", yes_tok
    elif 0.85 <= no_ask <= 0.99 and no_tok:
        side, token = "NO", no_tok
        yes_ask = no_ask
    else:
        return None
    return {
        "ts": signal_ts,
        "category": "tail_decay.signal",
        "validator": "tail-decay",
        "market_id": str(market.get("id")),
        "token": token,
        "side": side,
        "ask": yes_ask,
        "edge_pp": (1 - yes_ask) * 100,
        "past_deadline": False,
        "buy_dollars": 100,
        "slug": market.get("slug"),
        "_resolved": market.get("umaResolutionStatus") or
                     ("YES" if (market.get("outcomePrices") or [""])[0] == "1" else "NO"),
        "_synthesized": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--closed-since-days", type=int, default=60,
                    help="Look at markets closed in last N days")
    ap.add_argument("--days-before-close", type=int, default=3,
                    help="Synthesize signal N days before resolution")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", type=Path, default=SIGNALS_PATH)
    args = ap.parse_args()

    print(f"Pulling markets closed in last {args.closed_since_days} days...")
    markets = fetch_closed_markets(args.closed_since_days, args.limit)
    print(f"Got {len(markets)} closed markets. Synthesizing tail-decay signals...")
    n_synth = 0
    with args.out.open("a", encoding="utf-8") as f:
        for m in markets:
            sig = synthesize_tail_decay_signal(m, args.days_before_close)
            if sig:
                f.write(json.dumps(sig) + "\n")
                n_synth += 1
                if n_synth % 10 == 0:
                    print(f"  ... {n_synth} synthesized")
    print(f"Done. Wrote {n_synth} historical synthesized signals to {args.out}.")


if __name__ == "__main__":
    main()
