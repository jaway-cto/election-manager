"""
negrisk_scanner.py — Scan Polymarket multi-outcome (negRisk) events for
YES-sum overround opportunities.

Mechanic: a negRisk event has N mutually-exclusive outcomes. Exactly one
resolves YES. If sum_of_YES_asks - (N-1) < $1, you can BUY all N YES tokens
for a guaranteed $1 payout. After fees, you need SumASK < ~0.97 for net edge.

The reverse: if sum_of_YES_bids > $1, SELL all N (i.e. effectively short the
basket). Bid-side overround is rare — typically taker fees prevent it.

Stage 2 web validation showed: $29M was extracted by negRisk arbs Apr-2024
to Apr-2025, so the major slates are heavily contested. This scanner still
runs because (a) long-tail/new events emerge constantly, (b) sum-of-YES is
a useful metric for sanity-checking event slate completeness (implicit
"Other" = 1 - SumYES; if Other is large the slate is missing the frontrunner).

Usage:
    python negrisk_scanner.py
    python negrisk_scanner.py --watch 60
    python negrisk_scanner.py --min-edge-pp 1   # finer threshold
"""
from __future__ import annotations
import argparse
import concurrent.futures as futures
import sys
import time
from typing import Optional

import requests

from validator_core import (
    GAMMA, UA, parse_clob_token_ids, get_quote,
)
from notify import alert, fyi, event
import killswitch


def fetch_negrisk_events(limit: int = 200, max_pages: int = 5,
                         min_volume_24h: float = 500.0) -> list[dict]:
    """Pull events; filter to those tagged negRisk=True with non-trivial volume."""
    out: list[dict] = []
    for page in range(max_pages):
        try:
            r = requests.get(
                f"{GAMMA}/events",
                params={
                    "closed": "false", "active": "true",
                    "limit": limit, "offset": page * limit,
                    "order": "volume24hr", "ascending": "false",
                },
                headers=UA, timeout=20,
            )
            r.raise_for_status()
            data = r.json() or []
        except Exception as e:
            sys.stderr.write(f"events fetch failed: {e}\n")
            break
        if not data:
            break
        for ev in data:
            if not ev.get("negRisk"):
                continue
            if float(ev.get("volume24hr") or 0) < min_volume_24h:
                continue
            out.append(ev)
        if len(data) < limit:
            break
    return out


def _fetch_one_quote(token_id: str) -> tuple[str, object]:
    from validator_core import fetch_clob_book, quote_from_book
    return token_id, quote_from_book(token_id, fetch_clob_book(token_id, timeout=5.0))


def scan(min_edge_pp: float = 0.5, max_workers: int = 32,
         min_volume_24h: float = 500.0,
         deep_check_top: int = 10) -> list[dict]:
    """Use Gamma's bestBid/bestAsk fields per market — no CLOB calls in the
    initial scan. Optionally deep-check the top N candidates against live
    CLOB to confirm the edge before alerting.
    """
    if killswitch.tripped():
        fyi(f"negrisk_scanner halted: {killswitch.reason()}")
        return []
    events = fetch_negrisk_events(min_volume_24h=min_volume_24h)
    fyi(f"negrisk: {len(events)} negRisk events with > ${min_volume_24h:.0f} 24h vol")

    out: list[dict] = []
    for ev in events:
        ya = yb = ym = 0.0
        n = 0
        rows = []
        # First pass: detect if event already has a near-certain winner
        # (one outcome trading >= 99c). If so, skip the event entirely.
        has_winner = False
        for m in ev.get("markets") or []:
            if m.get("closed") or m.get("archived"):
                continue
            try:
                if float(m.get("bestBid") or 0) >= 0.99:
                    has_winner = True; break
            except (TypeError, ValueError):
                pass
        if has_winner:
            continue
        # CORRECTED: include "Another/Other" augmenter legs in basket
        # sum (red-team finding #3). Previously we filtered these out,
        # which made SumYES look <1.0 by construction = phantom edge.
        for m in ev.get("markets") or []:
            if m.get("closed") or m.get("archived"):
                continue
            ba = m.get("bestAsk")
            bb = m.get("bestBid")
            if ba is None or bb is None:
                continue
            try:
                ba_f = float(ba); bb_f = float(bb)
            except (TypeError, ValueError):
                continue
            if ba_f <= 0 and bb_f <= 0:
                continue
            mid = (ba_f + bb_f) / 2.0
            ya += ba_f; yb += bb_f; ym += mid
            n += 1
            git = (m.get("groupItemTitle") or "")
            is_augmenter = bool(any(p in git.lower() for p in
                                    ("another", "other ", "person ", "candidate ")))
            rows.append({
                "question": m.get("question", "")[:80],
                "bid": bb_f, "ask": ba_f, "mid": mid,
                "yes_token": parse_clob_token_ids(m)[0],
                "is_augmenter": is_augmenter,
            })
        if n < 2:
            continue
        out.append({
            "event_title": ev.get("title", "")[:100],
            "event_slug": ev.get("slug", ""),
            "event_id": ev.get("id"),
            "augmented": bool(ev.get("negRiskAugmented")),
            "n_markets": n,
            "yes_ask_sum": ya, "yes_bid_sum": yb, "yes_mid_sum": ym,
            "implicit_other": 1.0 - ym,
            "buy_basket_cost": ya,
            "buy_basket_edge_pp": (1.0 - ya) * 100,
            "sell_basket_proceeds": yb,
            "sell_basket_edge_pp": (yb - 1.0) * 100,
            "rows": rows,
            "volume24hr": float(ev.get("volume24hr") or 0),
        })
    out.sort(key=lambda r: -max(r["buy_basket_edge_pp"],
                                r["sell_basket_edge_pp"]))

    # Deep-check the top N candidates with live CLOB books, since Gamma
    # bestBid/bestAsk can lag by a few seconds.
    if deep_check_top > 0:
        for r in out[:deep_check_top]:
            if max(r["buy_basket_edge_pp"], r["sell_basket_edge_pp"]) < min_edge_pp:
                continue
            ya2 = yb2 = 0.0
            n2 = 0
            from validator_core import fetch_clob_book, quote_from_book
            for row in r["rows"]:
                tok = row.get("yes_token")
                if not tok:
                    continue
                book = fetch_clob_book(tok, timeout=5.0)
                if not book:
                    continue
                q = quote_from_book(tok, book)
                if not q.has_book:
                    continue
                ya2 += q.ask or 0
                yb2 += q.bid or 0
                n2 += 1
            if n2 == r["n_markets"]:
                r["clob_yes_ask_sum"] = ya2
                r["clob_yes_bid_sum"] = yb2
                r["clob_buy_edge_pp"] = (1.0 - ya2) * 100
                r["clob_sell_edge_pp"] = (yb2 - 1.0) * 100
    return out


def report(rows: list[dict], min_edge_pp: float = 0.5,
           top: int = 25) -> None:
    if not rows:
        print("(no negRisk events found)"); return
    print(f"\n{len(rows)} negRisk events, top {top} by edge:\n")
    hdr = (f"{'Event':<55} {'N':>3} {'SumAsk':>6} {'SumBid':>6} {'Other%':>7} "
           f"{'BuyEdge':>7} {'SellEdge':>8} {'Aug':>3} {'24h$':>9}")
    print(hdr); print("-" * len(hdr))
    flagged = 0
    for r in rows[:top]:
        title = r["event_title"][:55]
        v24 = f"${r['volume24hr']:>8,.0f}"
        aug = "Y" if r["augmented"] else "n"
        marker_b = "*" if r["buy_basket_edge_pp"] >= min_edge_pp else " "
        marker_s = "*" if r["sell_basket_edge_pp"] >= min_edge_pp else " "
        if "*" in (marker_b, marker_s):
            flagged += 1
        print(f"{title:<55} {r['n_markets']:>3} "
              f"{r['yes_ask_sum']:>6.3f} {r['yes_bid_sum']:>6.3f} "
              f"{r['implicit_other']*100:>+6.1f}% "
              f"{r['buy_basket_edge_pp']:>+5.1f}{marker_b} "
              f"{r['sell_basket_edge_pp']:>+6.1f}{marker_s} "
              f"{aug:>3} {v24:>9}")
    print(f"\n{flagged} events with |edge| >= {min_edge_pp}pp (Gamma snapshot).")
    print("Buy-basket edge: BUY all YES at ask, lock $1 payoff.")
    print("Sell-basket edge: SELL all YES at bid (or BUY all NO at ask).")
    print("Implicit Other%: large positive = slate likely missing the frontrunner.")
    # CLOB-confirmed edges (re-queried live for top candidates)
    confirmed = [r for r in rows if r.get("clob_buy_edge_pp") is not None
                 and (r["clob_buy_edge_pp"] >= min_edge_pp
                      or r["clob_sell_edge_pp"] >= min_edge_pp)]
    if confirmed:
        print(f"\nCLOB-confirmed live edges:")
        for r in confirmed:
            print(f"  {r['event_title'][:55]:<55}  "
                  f"buy {r['clob_buy_edge_pp']:+5.2f}pp  "
                  f"sell {r['clob_sell_edge_pp']:+5.2f}pp  "
                  f"(Gamma: buy {r['buy_basket_edge_pp']:+.2f} / "
                  f"sell {r['sell_basket_edge_pp']:+.2f})")
    # Per red-team findings: enforce minimum gross edge AND per-leg edge,
    # AND subtract NegRisk multi-leg surcharge (2.04%) before alerting.
    from validator_core import (
        NEGRISK_BASKET_MIN_GROSS_BPS, NEGRISK_PER_LEG_MIN_BPS,
        NEGRISK_SURCHARGE_BPS,
    )
    NEGRISK_MIN_GROSS_PP = NEGRISK_BASKET_MIN_GROSS_BPS / 100.0
    NEGRISK_SURCHARGE_PP = NEGRISK_SURCHARGE_BPS / 100.0
    NEGRISK_MIN_PER_LEG_PP = NEGRISK_PER_LEG_MIN_BPS / 100.0

    for r in rows[:5]:
        # Per-leg minimum: compute the implied per-leg cushion
        per_leg_avg = ((1.0 - r["yes_ask_sum"]) / max(r["n_markets"], 1)) * 100
        for direction, gross_edge_pp, sum_label, sum_val in (
            ("BUY", r["buy_basket_edge_pp"], "SumAsk", r["yes_ask_sum"]),
            ("SELL", r["sell_basket_edge_pp"], "SumBid", r["yes_bid_sum"]),
        ):
            net_after_surcharge = gross_edge_pp - NEGRISK_SURCHARGE_PP
            if (gross_edge_pp >= NEGRISK_MIN_GROSS_PP
                    and abs(per_leg_avg) >= NEGRISK_MIN_PER_LEG_PP
                    and net_after_surcharge >= min_edge_pp):
                alert(
                    f"negRisk {direction} basket {r['event_title'][:60]} - "
                    f"gross {gross_edge_pp:+.2f}pp, "
                    f"net {net_after_surcharge:+.2f}pp after 2.04% surcharge, "
                    f"per-leg avg {per_leg_avg:+.2f}pp, "
                    f"{sum_label} {sum_val:.3f}"
                )
                event("negrisk.signal", {
                    "event_id": r["event_id"], "side": direction,
                    "gross_pp": gross_edge_pp,
                    "net_pp": net_after_surcharge,
                    "per_leg_pp": per_leg_avg,
                    "sum": sum_val,
                })
                break  # one alert per event


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-edge-pp", type=float, default=0.5)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-volume-24h", type=float, default=10_000.0,
                    help="Only scan events with > $X 24h volume (default 10k)")
    ap.add_argument("--watch", type=int, default=0)
    args = ap.parse_args()
    while True:
        try:
            rows = scan(args.min_edge_pp, min_volume_24h=args.min_volume_24h)
            report(rows, args.min_edge_pp, args.top)
        except Exception as e:
            sys.stderr.write(f"scan error: {e}\n")
            import traceback; traceback.print_exc()
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
