"""
check_resolutions.py — For each captured signal, look up the current market
state and report whether the flagged edge actually paid out.

Workflow:
  1. Read data/signals.jsonl
  2. For each signal, fetch the market by id from Gamma
  3. Determine resolution state:
       umaResolutionStatus == 'resolved' → look at outcomePrices to see
       which outcome won.
       Otherwise the market is still pending.
  4. Compute paper P&L: if signal said BUY YES @ 0.93 and market resolved
     YES, the position would have paid $1.00 → +7¢ per share.
  5. Print a per-signal table + aggregate hit rate.

This bypasses the CLOB prices-history endpoint (which is sparse for closed
markets) and uses the more reliable Gamma /markets/{id} status fields.

Usage:
    python check_resolutions.py
    python check_resolutions.py --signals path/to/signals.jsonl
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Optional

import requests

GAMMA = "https://gamma-api.polymarket.com"
UA = {"User-Agent": "odds-resolution-checker"}


def fetch_market(market_id: str) -> Optional[dict]:
    try:
        r = requests.get(f"{GAMMA}/markets/{market_id}",
                         headers=UA, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        sys.stderr.write(f"[gamma] {market_id}: {e}\n")
        return None


def resolved_outcome(market: dict) -> Optional[str]:
    """Returns 'YES', 'NO', or None if not resolved."""
    uma = (market.get("umaResolutionStatus") or "").lower()
    if uma != "resolved":
        return None
    op = market.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            return None
    if not op or len(op) < 2:
        return None
    try:
        if float(op[0]) >= 0.99:
            return "YES"
        if float(op[1]) >= 0.99:
            return "NO"
    except (TypeError, ValueError):
        pass
    return None


def replay_signal(sig: dict) -> dict:
    market_id = str(sig.get("market_id", ""))
    if not market_id:
        return {"sig": sig, "status": "no_market_id"}
    m = fetch_market(market_id)
    if not m:
        return {"sig": sig, "status": "fetch_failed"}

    side = (sig.get("side") or sig.get("side_label") or "").upper()
    ask = float(sig.get("ask") or sig.get("entry_price") or 0)
    edge_pp = float(sig.get("edge_pp") or 0)
    bought_dollars = float(sig.get("buy_dollars") or 0)
    shares = bought_dollars / ask if ask > 0 else 0
    question = (m.get("question") or sig.get("slug") or "")[:80]

    outcome = resolved_outcome(m)
    closed = bool(m.get("closed"))

    # Determine if our hypothetical buy paid off
    pnl = None
    pnl_pct = None
    status = ""
    if outcome is None:
        if closed:
            status = "closed_unresolved"
        else:
            status = "open"
    else:
        # We bought side at price ask. If outcome matches our side, we get $1/share.
        # Otherwise, $0/share.
        won = (side == outcome)
        per_share_pnl = (1.0 - ask) if won else (-ask)
        pnl = per_share_pnl * shares
        pnl_pct = per_share_pnl / ask * 100
        status = f"resolved_{outcome.lower()}_{'win' if won else 'lose'}"

    return {
        "market_id": market_id,
        "question": question,
        "side": side, "ask": ask, "edge_pp": edge_pp,
        "bought_dollars": bought_dollars, "shares": shares,
        "outcome": outcome, "closed": closed,
        "uma_status": m.get("umaResolutionStatus"),
        "status": status, "pnl_dollars": pnl, "pnl_pct": pnl_pct,
        "current_best_bid": m.get("bestBid"),
        "current_best_ask": m.get("bestAsk"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", type=Path,
                    default=Path(r"C:\Dev\odds\data\signals.jsonl"))
    args = ap.parse_args()
    if not args.signals.exists():
        print(f"No signals file at {args.signals}")
        return

    seen_market_ids = set()
    results = []
    with args.signals.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                sig = json.loads(line)
            except Exception:
                continue
            mid = str(sig.get("market_id", ""))
            if not mid or mid in seen_market_ids:
                continue
            seen_market_ids.add(mid)
            r = replay_signal(sig)
            results.append(r)

    print(f"\nResolution check on {len(results)} unique signals\n")
    hdr = (f"{'Question':<55}{'Side':>4}{'Ask':>6}{'Status':>22}"
           f"{'PnL$':>8}{'PnL%':>7}")
    print(hdr); print("-" * len(hdr))

    n_resolved = 0; n_won = 0; n_lost = 0
    total_pnl = 0.0
    total_invested = 0.0
    for r in results:
        if "error" in r.get("status", ""):
            print(f"  ERROR  {r}")
            continue
        q = r["question"][:55]
        side = r["side"][:4]
        ask = f"{r['ask']:.3f}"
        st = r["status"][:22]
        if r["pnl_dollars"] is not None:
            pnl_d = f"{r['pnl_dollars']:+.2f}"
            pnl_p = f"{r['pnl_pct']:+.1f}%"
            n_resolved += 1
            if r["pnl_dollars"] > 0: n_won += 1
            else: n_lost += 1
            total_pnl += r["pnl_dollars"]
            total_invested += r["bought_dollars"]
        else:
            pnl_d = "—"
            pnl_p = "—"
        print(f"{q:<55}{side:>4}{ask:>6}{st:>22}{pnl_d:>8}{pnl_p:>7}")

    print("-" * len(hdr))
    print(f"\nResolved: {n_resolved}/{len(results)}  "
          f"({n_won} wins, {n_lost} losses)")
    if n_resolved > 0:
        win_rate = n_won / n_resolved * 100
        print(f"Hit rate (resolved only): {win_rate:.0f}%")
    if total_invested > 0:
        print(f"Total notional invested: ${total_invested:.2f}")
        print(f"Total realised P&L:      ${total_pnl:+.2f}  "
              f"({total_pnl/total_invested*100:+.1f}% on capital)")
    pending = len(results) - n_resolved
    if pending > 0:
        print(f"Still pending resolution: {pending}")


if __name__ == "__main__":
    main()
