"""
mm_simulator.py — Paper-mode market maker simulator.

Picks a market, posts notional quotes inside the existing spread, simulates
fills against observed CLOB trades, and tracks paper PnL + accrued rewards.

This is NOT a live trading bot. It's the "watch what would have happened"
phase before committing real capital + signed orders.

Strategy:
  1. Quote BID and ASK at (best_bid + step, best_ask - step), capped to stay
     within rewardsMaxSpread of midpoint.
  2. On each tick, check CLOB last_trade_price. If a trade prints at our bid,
     we get filled long. If at our ask, filled short.
  3. Maintain inventory; rebalance by skewing quotes when |inventory| grows.
  4. Track P&L from fills + estimated reward accrual (proxy: time-eligible).

Usage:
    python mm_simulator.py --token <TOKEN_ID> --capital 200 --duration 600
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from validator_core import (
    CLOB, UA, get_quote, fetch_clob_book, fetch_clob_midpoint,
)
from notify import fyi, event
import killswitch


@dataclass
class MMState:
    inventory: float = 0.0          # signed shares (long+, short-)
    cash: float = 0.0               # paper cash from fills (in $)
    fills: int = 0
    bid_px: Optional[float] = None
    ask_px: Optional[float] = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    eligible_seconds: float = 0.0
    log: list[dict] = field(default_factory=list)


def fetch_last_trade(token_id: str) -> Optional[float]:
    try:
        r = requests.get(f"{CLOB}/last-trade-price",
                         params={"token_id": token_id},
                         headers=UA, timeout=10)
        if r.status_code != 200:
            return None
        return float(r.json().get("price", 0))
    except Exception:
        return None


def quote_pair(book_bid: float, book_ask: float, mid: float,
               max_spread_dollar: float, tick: float = 0.001,
               step_inside: float = 0.001,
               inventory_skew_pp: float = 0.5) -> tuple[float, float]:
    """Compute our (bid, ask). Stay within max_spread_dollar of mid both sides."""
    our_bid = round(min(book_bid + step_inside, mid - tick), 3)
    our_ask = round(max(book_ask - step_inside, mid + tick), 3)
    # Enforce rewardsMaxSpread
    floor_bid = mid - max_spread_dollar
    ceil_ask = mid + max_spread_dollar
    our_bid = max(our_bid, floor_bid)
    our_ask = min(our_ask, ceil_ask)
    return our_bid, our_ask


def simulate(token_id: str, capital_usd: float = 200.0,
             duration_s: int = 600, tick_period_s: float = 5.0,
             max_spread_dollar: float = 0.045) -> MMState:
    """Run a paper simulation for `duration_s` seconds."""
    if killswitch.tripped():
        fyi(f"mm_simulator halted: {killswitch.reason()}")
        return MMState()

    state = MMState()
    side_capital = capital_usd / 2.0
    last_seen_trade = None
    t_start = time.time()
    while time.time() - t_start < duration_s:
        if killswitch.tripped():
            break
        try:
            q = get_quote(token_id)
            if not q.has_book:
                time.sleep(tick_period_s); continue
            bid, ask, mid = q.bid, q.ask, q.mid
            our_bid, our_ask = quote_pair(
                bid, ask, mid, max_spread_dollar)

            # Sizes (recompute each tick — simple, ignore inventory skew for v1)
            state.bid_px, state.ask_px = our_bid, our_ask
            state.bid_size = side_capital / max(our_bid, 0.001)
            state.ask_size = side_capital / max(1 - our_ask, 0.001)

            # Fill simulation: did anyone trade through our quotes since last tick?
            trade = fetch_last_trade(token_id)
            if trade is not None and trade != last_seen_trade:
                # Single-trade fill assumption: if last trade <= our_bid, we got hit on bid
                if trade <= our_bid + 1e-9:
                    fill_size = min(state.bid_size, 100.0)  # paper cap
                    state.inventory += fill_size
                    state.cash -= fill_size * our_bid
                    state.fills += 1
                    state.log.append({"ts": time.time(), "side": "BUY",
                                      "px": our_bid, "size": fill_size})
                elif trade >= our_ask - 1e-9:
                    fill_size = min(state.ask_size, 100.0)
                    state.inventory -= fill_size
                    state.cash += fill_size * our_ask
                    state.fills += 1
                    state.log.append({"ts": time.time(), "side": "SELL",
                                      "px": our_ask, "size": fill_size})
                last_seen_trade = trade

            # Reward eligibility tracking
            if (mid - our_bid <= max_spread_dollar and
                our_ask - mid <= max_spread_dollar):
                state.eligible_seconds += tick_period_s

            # Periodic status
            if int(time.time() - t_start) % 60 == 0:
                pnl = state.cash + state.inventory * (mid or 0)
                print(f"  t+{int(time.time()-t_start):>4}s  "
                      f"bid {our_bid:.3f} ask {our_ask:.3f} mid {mid:.3f}  "
                      f"inv {state.inventory:>+5.0f} cash ${state.cash:>+7.2f}  "
                      f"mtm-pnl ${pnl:>+7.2f}  fills={state.fills}")
        except Exception as e:
            sys.stderr.write(f"sim tick error: {e}\n")
        time.sleep(tick_period_s)
    return state


def report(state: MMState, market_label: str = "") -> None:
    pnl_realised = state.cash
    # mtm: not available without final mid; caller can do externally
    print(f"\nFinal MM simulation result{(' for ' + market_label) if market_label else ''}:")
    print(f"  fills           = {state.fills}")
    print(f"  inventory (sh)  = {state.inventory:+.1f}")
    print(f"  cash ($)        = {state.cash:+.2f}")
    print(f"  eligible (sec)  = {state.eligible_seconds:.0f}")
    if state.log:
        print(f"  fills log:")
        for f in state.log:
            print(f"    {dt.datetime.fromtimestamp(f['ts']).strftime('%H:%M:%S')}  "
                  f"{f['side']:<4} {f['size']:>5.0f} @ {f['px']:.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="CLOB token id")
    ap.add_argument("--capital", type=float, default=200.0)
    ap.add_argument("--duration", type=int, default=600,
                    help="Simulation duration in seconds")
    ap.add_argument("--tick", type=float, default=5.0,
                    help="Tick period in seconds")
    ap.add_argument("--max-spread", type=float, default=0.045,
                    help="Max distance from mid (matches rewardsMaxSpread/100)")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    print(f"Starting paper MM simulation on token {args.token[:12]}... "
          f"capital ${args.capital}, duration {args.duration}s")
    state = simulate(args.token, args.capital, args.duration,
                     args.tick, args.max_spread)
    report(state, args.label)


if __name__ == "__main__":
    main()
