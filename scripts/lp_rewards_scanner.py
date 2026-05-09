"""
lp_rewards_scanner.py — Find Polymarket markets eligible for liquidity rewards
where a solo market-maker can compete (i.e., where the pros aren't already
saturating the book).

Polymarket CLOB v2 launched 2026-04-28 with a $1M liquidity rewards program.
Rewards parameters per market are exposed on the Gamma market object:

    rewardsMinSize     — minimum order size in shares to qualify (typically 50)
    rewardsMaxSpread   — max distance from midpoint your order can sit (cents)
    makerBaseFee       — fee in micro-bps; rewards offset this
    holdingRewardsEnabled — additional inventory-holding rewards

Strategy: rank rewards-eligible markets by (a) low existing CLOB liquidity
(less crowded), (b) non-zero recent volume (some trade flow), (c) reasonable
spread (book isn't broken).

Usage:
    python lp_rewards_scanner.py
    python lp_rewards_scanner.py --max-liquidity 50000   # only thinner markets
    python lp_rewards_scanner.py --watch 600
"""
from __future__ import annotations
import argparse
import concurrent.futures as futures
import datetime as dt
import sys
import time
from typing import Optional

import requests

from validator_core import (
    GAMMA, UA, parse_clob_token_ids, get_quote,
)
from notify import alert, fyi, event
import killswitch


def fetch_active_markets(limit: int = 500, max_pages: int = 6) -> list[dict]:
    """Pull active markets in pages."""
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(max_pages):
        try:
            r = requests.get(
                f"{GAMMA}/markets",
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
            sys.stderr.write(f"gamma /markets fetch failed: {e}\n")
            break
        if not data:
            break
        for m in data:
            mid = str(m.get("id", ""))
            if mid and mid not in seen:
                seen.add(mid)
                out.append(m)
        if len(data) < limit:
            break
    return out


def is_eligible(m: dict) -> bool:
    """Market participates in maker rewards program."""
    if m.get("closed") or m.get("archived") or not m.get("active", True):
        return False
    if not m.get("clobTokenIds"):
        return False
    rms = m.get("rewardsMinSize") or 0
    rmx = m.get("rewardsMaxSpread") or 0
    return float(rms) > 0 and float(rmx) > 0


def _enrich_with_book(m: dict) -> Optional[dict]:
    """Fetch CLOB book for YES side, compute live spread + depth."""
    yes_tok, no_tok = parse_clob_token_ids(m)
    if not yes_tok:
        return None
    q_yes = get_quote(yes_tok)
    q_no = get_quote(no_tok) if no_tok else None
    if not q_yes.has_book:
        return None
    return {
        "market_id": str(m.get("id", "")),
        "question": m.get("question", ""),
        "slug": m.get("slug", ""),
        "yes_token": yes_tok,
        "no_token": no_tok,
        "rewardsMinSize": float(m.get("rewardsMinSize") or 0),
        "rewardsMaxSpread": float(m.get("rewardsMaxSpread") or 0),
        "holdingRewardsEnabled": bool(m.get("holdingRewardsEnabled")),
        "liquidityClob": float(m.get("liquidityClob") or 0),
        "volume24hr": float(m.get("volume24hr") or 0),
        "yes_bid": q_yes.bid, "yes_ask": q_yes.ask,
        "yes_mid": q_yes.mid, "yes_spread_bps": q_yes.spread_bps,
        "yes_bid_size": q_yes.bid_size, "yes_ask_size": q_yes.ask_size,
        "no_mid": (q_no.mid if q_no else None),
        "endDate": m.get("endDate"),
        "tickSize": float(m.get("orderPriceMinTickSize") or 0.001),
    }


def scan(min_volume_24h: float = 100.0,
         max_liquidity: float = 200_000.0,
         min_days_to_end: int = 1,
         max_workers: int = 24) -> list[dict]:
    """Return rewards-eligible markets ranked by attractiveness."""
    if killswitch.tripped():
        fyi(f"lp_rewards_scanner halted: {killswitch.reason()}")
        return []

    all_markets = fetch_active_markets()
    eligible = [m for m in all_markets if is_eligible(m)]
    fyi(f"lp-rewards: {len(all_markets)} active, {len(eligible)} rewards-eligible")

    # Pre-filter cheaply by metadata before fetching books
    now = dt.datetime.now(dt.timezone.utc)
    pruned: list[dict] = []
    for m in eligible:
        v = float(m.get("volume24hr") or 0)
        liq = float(m.get("liquidityClob") or 0)
        if v < min_volume_24h:
            continue
        if liq > max_liquidity:
            continue  # too crowded
        end_iso = m.get("endDate") or ""
        try:
            end_dt = dt.datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            if (end_dt - now).days < min_days_to_end:
                continue
        except Exception:
            pass
        pruned.append(m)

    fyi(f"lp-rewards: {len(pruned)} markets passed pre-filter, fetching books")
    enriched: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_enrich_with_book, pruned):
            if r:
                enriched.append(r)

    # Score: lower liquidity + decent volume + sane spread = better for solo MM.
    # We want existing book to be wide enough that we can quote inside.
    for r in enriched:
        sp = r.get("yes_spread_bps") or 0
        liq = max(r["liquidityClob"], 1.0)
        vol = max(r["volume24hr"], 1.0)
        # Reward signal: high vol, low liq, wide spread (we can tighten and earn)
        r["mm_score"] = (vol / liq) * (sp / 100.0) * (1.0 if r["holdingRewardsEnabled"] else 0.5)
    enriched.sort(key=lambda r: -r["mm_score"])
    return enriched


def report(rows: list[dict], top: int = 25) -> None:
    if not rows:
        print("(no eligible markets matched filters)"); return
    print(f"\n{len(rows)} rewards-eligible markets (top {top} by mm_score):\n")
    hdr = (f"{'Q':<48} {'Mid':>5} {'Spr':>5} {'MinSz':>5} {'MaxSp':>5} "
           f"{'Liq$':>9} {'24h$':>9} {'HoldR':>5} {'Score':>6}")
    print(hdr); print("-" * len(hdr))
    for r in rows[:top]:
        q = (r["question"] or "")[:48]
        mid = f"{r['yes_mid']:.3f}" if r["yes_mid"] is not None else "  -  "
        sp = f"{r['yes_spread_bps']:.0f}bp" if r["yes_spread_bps"] else "  -  "
        liq = f"${r['liquidityClob']:>8,.0f}"
        v24 = f"${r['volume24hr']:>8,.0f}"
        hold = "Y" if r["holdingRewardsEnabled"] else "n"
        print(f"{q:<48} {mid:>5} {sp:>5} {r['rewardsMinSize']:>5.0f} "
              f"{r['rewardsMaxSpread']:>5.1f} {liq:>9} {v24:>9} {hold:>5} "
              f"{r['mm_score']:>6.2f}")


def quote_plan(market: dict, capital_usd: float = 200.0,
               edge_bps_target: int = 100) -> dict:
    """For a chosen market, compute the post-only quote pair we'd place.

    Quote inside the existing spread to win the touch but stay within
    rewardsMaxSpread of the midpoint to remain eligible for rewards.

    Args:
        capital_usd: total inventory to deploy across both sides
        edge_bps_target: how far inside best bid/ask we step (1pp = 100bps)
    Returns: {bid_price, ask_price, bid_shares, ask_shares, eligible_for_rewards}
    """
    bid = market["yes_bid"] or 0.4
    ask = market["yes_ask"] or 0.6
    mid = market["yes_mid"] or (bid + ask) / 2.0
    tick = market["tickSize"] or 0.001
    max_spread = market["rewardsMaxSpread"] / 100.0  # convert ¢ → $
    step = max(tick, edge_bps_target / 10000.0)
    our_bid = round(min(bid + step, mid - tick), 3)
    our_ask = round(max(ask - step, mid + tick), 3)
    eligible = (mid - our_bid <= max_spread) and (our_ask - mid <= max_spread)
    side_capital = capital_usd / 2.0
    bid_shares = max(market["rewardsMinSize"], side_capital / max(our_bid, tick))
    ask_shares = max(market["rewardsMinSize"], side_capital / max(1 - our_ask, tick))
    return {
        "market_id": market["market_id"],
        "question": market["question"],
        "yes_token": market["yes_token"],
        "no_token": market["no_token"],
        "our_bid": our_bid, "our_ask": our_ask,
        "current_bid": bid, "current_ask": ask,
        "mid": mid,
        "bid_shares": bid_shares, "ask_shares": ask_shares,
        "eligible_for_rewards": eligible,
        "rewardsMinSize": market["rewardsMinSize"],
        "rewardsMaxSpread": market["rewardsMaxSpread"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-volume-24h", type=float, default=100.0)
    ap.add_argument("--max-liquidity", type=float, default=200_000.0)
    ap.add_argument("--min-days", type=int, default=1)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--watch", type=int, default=0)
    ap.add_argument("--quote-plan", action="store_true",
                    help="Print quote-pair plan for top N markets")
    ap.add_argument("--capital", type=float, default=200.0,
                    help="Capital per market for quote plan")
    args = ap.parse_args()

    while True:
        try:
            rows = scan(args.min_volume_24h, args.max_liquidity, args.min_days)
            report(rows, args.top)
            if args.quote_plan:
                print("\nQuote plans (top 5):")
                for r in rows[:5]:
                    plan = quote_plan(r, args.capital)
                    print(f"\n  {plan['question'][:70]}")
                    print(f"    book: {plan['current_bid']:.3f} / {plan['current_ask']:.3f}  "
                          f"mid {plan['mid']:.3f}")
                    print(f"    quote: BID {plan['our_bid']:.3f} ({plan['bid_shares']:.0f} sh) | "
                          f"ASK {plan['our_ask']:.3f} ({plan['ask_shares']:.0f} sh)")
                    print(f"    eligible_for_rewards: {plan['eligible_for_rewards']}")
        except Exception as e:
            sys.stderr.write(f"scan error: {e}\n")
            import traceback; traceback.print_exc()
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
