"""
cross_venue_pm_betfair.py — Polymarket × Betfair Exchange arbitrage scanner.

For each (pm_slug, bf_market_id, bf_runner_name) pair in
data/pm_betfair_pairs.csv, fetch:
  - PM CLOB ask + bid for the YES outcome
  - Betfair best back + lay for the runner
Then check both arb directions:
  * BUY YES on PM (cost = pm_ask) + LAY @ Betfair (collect 1/lay_price stake)
  * SELL YES on PM (collect pm_bid) + BACK @ Betfair (collect back_price - 1)

Includes commission (5% Betfair default) and Polymarket taker fee from the
market's feeSchedule.

Without Betfair credentials, the script prints which pairs would be checked
but cannot fetch prices. Set BETFAIR_APP_KEY/USERNAME/PASSWORD to enable.

Usage:
    python cross_venue_pm_betfair.py
    python cross_venue_pm_betfair.py --watch 120
    python cross_venue_pm_betfair.py --pairs path/to/custom.csv
"""
from __future__ import annotations
import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Optional

from validator_core import (
    gamma_event, gamma_search, parse_clob_token_ids, get_quote, fees_for_market,
)
from venues.betfair_client import BetfairClient
from notify import alert, fyi, event
import killswitch


PAIRS_CSV = Path(r"C:\Dev\odds\data\pm_betfair_pairs.csv")
BETFAIR_COMMISSION = 0.05  # 5% on net winnings on Betfair Exchange


def load_pairs(path: Path = PAIRS_CSV) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("pm_slug") or not r.get("bf_market_id"):
                continue  # rows without bf_market_id are placeholders
            rows.append(r)
    return rows


def find_pm_market(pm_slug: str, label: str) -> Optional[dict]:
    """For multi-outcome events, label may be a runner / candidate name."""
    ev = gamma_event(pm_slug)
    if not ev:
        return None
    markets = ev.get("markets") or []
    if not label:
        return markets[0] if markets else None
    label_lower = label.lower()
    # Try groupItemTitle first (politicians / candidates)
    for m in markets:
        git = (m.get("groupItemTitle") or "").lower()
        if git and label_lower in git:
            return m
    for m in markets:
        q = (m.get("question") or "").lower()
        if label_lower in q:
            return m
    return None


def evaluate_pair(row: dict, bf: BetfairClient) -> Optional[dict]:
    if not bf.authed:
        return None
    pm_market = find_pm_market(row["pm_slug"], row.get("pm_label", ""))
    if not pm_market:
        return {"row": row, "error": "PM market not found"}
    yes_tok, _ = parse_clob_token_ids(pm_market)
    if not yes_tok:
        return {"row": row, "error": "no clobTokenIds on PM market"}
    pm_q = get_quote(yes_tok)
    if not pm_q.has_book:
        return {"row": row, "error": "PM book empty"}

    # Betfair fetch
    try:
        bf_books = bf.list_market_book([row["bf_market_id"]])
    except Exception as e:
        return {"row": row, "error": f"betfair fetch failed: {e}"}
    if not bf_books:
        return {"row": row, "error": "BF market not found"}
    pair = bf.best_lay_back(bf_books[0], row.get("bf_runner_name", ""))
    if not pair:
        return {"row": row, "error": "BF runner not found"}
    bf_back, bf_lay = pair  # decimal odds
    if bf_back is None or bf_lay is None:
        return {"row": row, "error": "BF prices missing"}

    # Convert Betfair decimal odds → implied probability
    bf_back_prob = 1.0 / bf_back  # what you'd implicitly buy at by BACKing
    bf_lay_prob = 1.0 / bf_lay    # what you'd implicitly sell at by LAYing

    # PM fees
    taker_bps, _ = fees_for_market(pm_market)
    pm_taker_frac = taker_bps / 10000.0

    # Direction 1: BUY YES on PM, LAY on Betfair (sells YES on Betfair).
    # Net edge = bf_lay_prob - pm_q.ask  (gross), minus PM taker fee minus BF commission
    pm_ask = pm_q.ask or 1.0
    pm_bid = pm_q.bid or 0.0
    edge_buy_pm_lay_bf = (bf_lay_prob - pm_ask) - pm_taker_frac
    # Account for BF commission on winnings (only paid on net BF profit)
    if edge_buy_pm_lay_bf > 0:
        edge_buy_pm_lay_bf -= BETFAIR_COMMISSION * (1.0 - pm_ask)

    # Direction 2: SELL YES on PM, BACK on Betfair (buys YES on Betfair).
    edge_sell_pm_back_bf = (pm_bid - bf_back_prob) - pm_taker_frac
    if edge_sell_pm_back_bf > 0:
        edge_sell_pm_back_bf -= BETFAIR_COMMISSION * (bf_back - 1.0) / bf_back

    return {
        "row": row,
        "pm_market_id": pm_market.get("id"),
        "pm_question": pm_market.get("question"),
        "pm_bid": pm_bid, "pm_ask": pm_ask,
        "pm_taker_bps": taker_bps,
        "bf_back": bf_back, "bf_lay": bf_lay,
        "bf_back_prob": bf_back_prob, "bf_lay_prob": bf_lay_prob,
        "edge_buy_pm_lay_bf_pp": edge_buy_pm_lay_bf * 100,
        "edge_sell_pm_back_bf_pp": edge_sell_pm_back_bf * 100,
    }


def scan(pairs_csv: Path = PAIRS_CSV, min_edge_pp: float = 0.5) -> list[dict]:
    if killswitch.tripped():
        fyi(f"cross-venue PM×Betfair halted: {killswitch.reason()}")
        return []
    pairs = load_pairs(pairs_csv)
    if not pairs:
        fyi(f"no PM-Betfair pairs in {pairs_csv} (fill in bf_market_id columns)")
        return []
    bf = BetfairClient.from_env()
    if not bf.creds.ready():
        fyi("BETFAIR_* creds not set — skipping PM-Betfair scan")
        return []
    if not bf.login():
        fyi("Betfair login failed — skipping PM-Betfair scan")
        return []
    out: list[dict] = []
    for r in pairs:
        result = evaluate_pair(r, bf)
        if result is None:
            continue
        if "error" in result:
            event("pm_betfair.skip", {"row": r, "error": result["error"]})
            continue
        out.append(result)
        max_edge = max(result["edge_buy_pm_lay_bf_pp"],
                       result["edge_sell_pm_back_bf_pp"])
        if max_edge >= min_edge_pp:
            direction = ("BUY PM / LAY BF"
                         if result["edge_buy_pm_lay_bf_pp"] > result["edge_sell_pm_back_bf_pp"]
                         else "SELL PM / BACK BF")
            alert(
                f"PM-Betfair arb {max_edge:+.2f}pp NET — {direction}\n"
                f"  PM: {result['pm_question'][:80]}\n"
                f"  PM bid {result['pm_bid']:.3f} / ask {result['pm_ask']:.3f}\n"
                f"  BF back {result['bf_back']:.2f} ({result['bf_back_prob']*100:.1f}%) / "
                f"lay {result['bf_lay']:.2f} ({result['bf_lay_prob']*100:.1f}%)"
            )
            event("pm_betfair.signal", result)
    return out


def report(rows: list[dict]) -> None:
    if not rows:
        print("(no edges to report)"); return
    rows_sorted = sorted(rows, key=lambda r: -max(
        r["edge_buy_pm_lay_bf_pp"], r["edge_sell_pm_back_bf_pp"]))
    hdr = (f"{'Question':<55}{'PM ask':>7}{'PM bid':>7}"
           f"{'BF back':>8}{'BF lay':>7}{'BUY/LAY':>8}{'SELL/BACK':>10}")
    print(hdr); print("-" * len(hdr))
    for r in rows_sorted:
        print(f"{(r['pm_question'] or '')[:55]:<55}{r['pm_ask']:>7.3f}"
              f"{r['pm_bid']:>7.3f}{r['bf_back']:>8.2f}{r['bf_lay']:>7.2f}"
              f"{r['edge_buy_pm_lay_bf_pp']:>+7.2f}pp"
              f"{r['edge_sell_pm_back_bf_pp']:>+9.2f}pp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, default=PAIRS_CSV)
    ap.add_argument("--min-edge-pp", type=float, default=0.5)
    ap.add_argument("--watch", type=int, default=0)
    args = ap.parse_args()
    while True:
        try:
            rows = scan(args.pairs, args.min_edge_pp)
            report(rows)
        except Exception as e:
            sys.stderr.write(f"scan error: {e}\n")
            import traceback; traceback.print_exc()
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
