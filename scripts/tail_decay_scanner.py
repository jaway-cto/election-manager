"""
tail_decay_scanner.py — Hunt for residual asks on near-resolved markets.

Strategy:
    Markets close within N days. The book sometimes has asks at 0.95-0.999
    on outcomes that are mathematically certain (sports game finished but
    resolution pending, prices already breached threshold, etc.). Buying
    these and holding to redemption = ~free money.

This first iteration is READ-ONLY. It surfaces candidates to a Telegram
alert + the SQLite log. No orders sent until the operator explicitly
flips PM_TRADING_ENABLED=1 and we add the auto-buy step.

Filters:
    * Market not yet closed
    * endDate within --max-days (default 7)
    * Best ask <= --max-ask (default 0.99)
    * Best ask >= --min-ask (default 0.85) so we ignore truly mispriced or thin markets
    * Spread sane (filtered downstream by validator_core if too wide)
    * NOT in the "skip-if-subjective" list (manual ban list of slug patterns)

Heuristic for "outcome looks resolved":
    We don't try to read the resolution criteria text and verify externally
    in v1. Instead, we surface ALL candidates passing the price/time filters
    to the operator, and flag any whose volume24h is near zero (stale market,
    likely already determined) vs still-active.

Usage:
    python tail_decay_scanner.py
    python tail_decay_scanner.py --max-days 3 --max-ask 0.97
    python tail_decay_scanner.py --watch 300
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
    GAMMA, UA, get_quote, parse_clob_token_ids, fetch_oi, gamma_event,
)
from market_classifier import safe_for_tail_decay, classify, Shape, Status
from notify import alert, fyi, event
import killswitch
from clob_client import client as clob


SUBJECTIVE_BANLIST = [
    "substantially", "primarily", "mostly", "considered", "deemed",
    "interpreted", "subjective", "appears", "perceived", "generally",
]

# Positive whitelist: resolution criteria that are quantitative + public-data
# verifiable (red-team finding #2). Markets matching one of these patterns
# are eligible for auto-execute; everything else requires manual review.
import re
OBJECTIVE_PATTERNS = [
    re.compile(r"\bclos(e|es|ing)\s+(price|value)\b", re.I),
    re.compile(r"\bofficial\s+(result|score|count)", re.I),
    re.compile(r"\b(coinbase|binance|kraken|deribit)\b.*\b(price|close|spot)", re.I),
    re.compile(r"\baccording\s+to\s+(reuters|ap|bbc|bloomberg)", re.I),
    re.compile(r"\b(per|by)\s+\d+(\:\d+)?\s*(am|pm|et|utc|gmt)", re.I),
    re.compile(r"\b(at|>=|≥|<=|≤|above|below|exceeds)\s*\$?\d", re.I),
]


def fetch_closing_markets(max_days: int = 7, limit: int = 500) -> list[dict]:
    """Pull markets ending in the next `max_days`. (Note: Gamma `endDate`
    is unreliable for ladder legs — we re-derive the deadline downstream
    via market_classifier.effective_deadline. This fetch is intentionally
    permissive to capture ladder legs even when their endDate is stale.)"""
    end_max = (dt.datetime.now(dt.timezone.utc)
               + dt.timedelta(days=max_days)).isoformat()
    out: list[dict] = []
    seen: set[str] = set()
    for offset in range(0, 2000, limit):
        try:
            r = requests.get(
                f"{GAMMA}/markets",
                params={
                    "closed": "false", "active": "true",
                    "end_date_max": end_max,
                    "limit": limit, "offset": offset,
                    "order": "endDate", "ascending": "true",
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
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append(m)
        if len(data) < limit:
            break
    return out


# In-process cache: avoid re-fetching the same event for sibling legs
_EVENT_CACHE: dict[str, dict] = {}


def fetch_event_for_market(market: dict) -> Optional[dict]:
    """Fetch parent event so we can examine sibling legs (calendar ladders)."""
    # Markets carry an 'events' list with the parent event slug
    events_meta = market.get("events") or []
    if events_meta:
        slug = events_meta[0].get("slug")
        if slug:
            if slug in _EVENT_CACHE:
                return _EVENT_CACHE[slug]
            ev = gamma_event(slug)
            if ev:
                _EVENT_CACHE[slug] = ev
                return ev
    return None


def is_subjective(market: dict) -> bool:
    desc = (market.get("description") or "") + " " + (market.get("question") or "")
    desc = desc.lower()
    return any(b in desc for b in SUBJECTIVE_BANLIST)


def has_objective_resolution(market: dict) -> bool:
    """Positive whitelist — at least one objective resolution-criteria
    pattern present. Required for auto-execute (red-team finding #2).
    """
    desc = (market.get("description") or "") + " " + (market.get("question") or "")
    return any(p.search(desc) for p in OBJECTIVE_PATTERNS)


def _eval_market(m: dict, min_ask: float, max_ask: float,
                 min_size_usd: float, per_market_cap_usd: float) -> list[dict]:
    """Per-market worker — uses market_classifier to apply the source-level
    safety check before any price evaluation. Returns 0-2 candidate rows."""
    if m.get("closed") or m.get("archived") or not m.get("active", True):
        return []
    if is_subjective(m):
        return []
    # SOURCE FIX: classify market in event context BEFORE treating endDate
    # as a tail-decay signal. This catches calendar-ladder legs whose
    # successor brackets are still live (e.g. Trump-China March 31 vs
    # May 15/31 + June 30).
    event = fetch_event_for_market(m)
    safe, reason = safe_for_tail_decay(m, event)
    if not safe:
        return []
    classification = classify(m, event)
    yes_tok, no_tok = parse_clob_token_ids(m)
    out: list[dict] = []
    for tok, side_label in ((yes_tok, "YES"), (no_tok, "NO")):
        if not tok:
            continue
        q = get_quote(tok)
        if not q.has_book or q.ask is None:
            continue
        if not (min_ask <= q.ask <= max_ask):
            continue
        if q.ask_size is None or q.ask * q.ask_size < min_size_usd:
            continue
        if q.spread_bps is not None and q.spread_bps > 1500:
            continue
        buy_dollars = min(q.ask * q.ask_size, per_market_cap_usd)
        edge_pp = (1.0 - q.ask) * 100
        deadline = classification.effective_deadline
        past_deadline = (deadline is not None
                         and deadline < dt.datetime.now(dt.timezone.utc))
        out.append({
            "market_id": m.get("id"),
            "question": m.get("question"),
            "slug": m.get("slug"),
            "side_label": side_label,
            "token": tok,
            "ask": q.ask, "ask_size": q.ask_size,
            "spread_bps": q.spread_bps,
            "end_date": deadline.isoformat() if deadline else "",
            "past_deadline": past_deadline,
            "buy_shares": buy_dollars / q.ask,
            "buy_dollars": buy_dollars,
            "edge_pp": edge_pp,
            "volume_24h": m.get("volume24hr") or 0,
            "shape": classification.shape.value,
            "status": classification.status.value,
        })
    return out


def scan(max_days: int = 7, min_ask: float = 0.85,
         max_ask: float = 0.99, min_size_usd: float = 50.0,
         per_market_cap_usd: float = 200.0,
         max_workers: int = 24) -> list[dict]:
    """Return candidate rows; do not place orders. Parallel CLOB fetch."""
    if killswitch.tripped():
        fyi(f"tail_decay_scanner halted: {killswitch.reason()}")
        return []
    markets = fetch_closing_markets(max_days)
    # Pre-filter cheaply: only markets with non-zero recent activity, valid tokens.
    pruned = [m for m in markets
              if m.get("clobTokenIds")
              and not m.get("closed") and not m.get("archived")
              and m.get("active", True)
              and (m.get("volume24hr") or 0) > 0]
    t0 = time.time()
    fyi(f"tail-decay scan: {len(markets)} markets total, {len(pruned)} active "
        f"with volume — fetching books in parallel ({max_workers} workers)")
    candidates: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for rows in ex.map(
            lambda m: _eval_market(m, min_ask, max_ask, min_size_usd,
                                   per_market_cap_usd),
            pruned,
        ):
            candidates.extend(rows)
    fyi(f"tail-decay scan finished in {time.time()-t0:.1f}s — "
        f"{len(candidates)} candidates")
    candidates.sort(key=lambda r: -r["edge_pp"])
    return candidates


def report(rows: list[dict], max_alert: int = 5) -> None:
    if not rows:
        fyi("tail-decay: no candidates found")
        return
    past = [r for r in rows if r["past_deadline"]]
    upcoming = [r for r in rows if not r["past_deadline"]]
    print(f"\n{len(rows)} tail-decay candidates "
          f"({len(past)} past-deadline, {len(upcoming)} upcoming):\n")
    hdr = (f"{'Q':<55} {'Side':>4} {'Ask':>6} {'Size':>7} {'Edge':>6} "
           f"{'24h$':>8} {'!':>1}  EndDate")
    print(hdr); print("-" * len(hdr))
    # Past-deadline first (highest-confidence tail-decay)
    for r in (past + upcoming)[:25]:
        q = (r["question"] or "")[:55]
        v24 = f"${(r['volume_24h'] or 0):,.0f}"
        flag = "*" if r["past_deadline"] else " "
        print(f"{q:<55} {r['side_label']:>4} {r['ask']:>6.3f} "
              f"{r['ask_size']:>7.0f} {r['edge_pp']:>5.1f}% {v24:>8} {flag:>1}  "
              f"{(r['end_date'] or '')[:10]}")
    if past:
        print("\n* = endDate already passed (UMA likely to resolve any moment) — "
              "these are highest-confidence tail-decay candidates")
    # Alert on top N — past-deadline first
    for r in (past + upcoming)[:max_alert]:
        tag = "PAST-DEADLINE" if r["past_deadline"] else "tail-decay"
        alert(
            f"`{tag}` *{r['side_label']}* @ {r['ask']:.3f} "
            f"(edge {r['edge_pp']:.1f}pp) — {(r['question'] or '')[:80]} "
            f"→ ends {(r['end_date'] or '')[:10]} — "
            f"buy ${r['buy_dollars']:.0f} max",
        )
        event("tail_decay.signal", {
            "market_id": r["market_id"], "side": r["side_label"],
            "ask": r["ask"], "edge_pp": r["edge_pp"],
            "past_deadline": r["past_deadline"],
            "buy_dollars": r["buy_dollars"], "slug": r["slug"],
        })


def execute_buys(rows: list[dict], *, per_market_cap: float = 100.0,
                 daily_cap_usd: float = 300.0, min_edge_pp: float = 3.0,
                 only_past_deadline: bool = False,
                 require_objective: bool = True,
                 max_executions_per_run: int = 3,
                 dry_run: bool = True) -> list[dict]:
    """Place BUY orders for the highest-confidence candidates.

    Hard gates (any fail = no order):
      * killswitch armed
      * clob.creds.trading_enabled
      * dry_run flag respected (returns plan, no orders)
      * edge_pp >= min_edge_pp
      * if only_past_deadline=True, skip upcoming-deadline candidates
      * total spend this call <= daily_cap_usd
      * spread re-checked at execute time (must still be <1500bps)
      * positions.sqlite open-position cap not exceeded

    Returns list of {row, status, order_id|reason}.
    """
    if killswitch.tripped():
        fyi(f"tail-decay execute halted: {killswitch.reason()}")
        return []
    if not dry_run:
        try:
            clob.require_trading()
        except Exception as e:
            fyi(f"tail-decay execute aborted: {e}")
            return []

    import positions as pos
    open_positions = pos.list_open()
    open_market_ids = {r["market_id"] for r in open_positions if r["validator"] == "tail-decay"}

    plan: list[dict] = []
    spent = 0.0
    executions = 0
    for r in rows:
        if executions >= max_executions_per_run:
            break
        if only_past_deadline and not r["past_deadline"]:
            continue
        if r["edge_pp"] < min_edge_pp:
            continue
        if r["market_id"] in open_market_ids:
            continue  # avoid double-up on same market
        # Red-team finding #2: positive whitelist gate
        if require_objective:
            # We need the original market dict; we stored question only.
            # Re-fetch to check resolution criteria.
            from validator_core import gamma_search
            res = gamma_search(r["question"][:60], limit=3)
            mk = None
            for src in (res.get("markets") or [], res.get("events") or []):
                for it in src:
                    if str(it.get("id")) == str(r["market_id"]):
                        mk = it
                        break
                    for sub in it.get("markets", []) or []:
                        if str(sub.get("id")) == str(r["market_id"]):
                            mk = sub
                            break
                if mk:
                    break
            if mk and not has_objective_resolution(mk):
                plan.append({"row": r, "status": "skip",
                             "reason": "no objective resolution criteria match"})
                continue
        # Spread re-check: book may have changed; refetch
        from validator_core import get_quote
        q = get_quote(r["token"])
        if not q.has_book or q.ask is None:
            plan.append({"row": r, "status": "skip",
                         "reason": "book gone since scan"})
            continue
        # Don't execute if book moved against us by >1pp
        if abs(q.ask - r["ask"]) > 0.01:
            plan.append({"row": r, "status": "skip",
                         "reason": f"price moved {r['ask']:.3f}->{q.ask:.3f}"})
            continue
        if q.spread_bps and q.spread_bps > 1500:
            plan.append({"row": r, "status": "skip",
                         "reason": f"spread widened to {q.spread_bps:.0f}bps"})
            continue
        size_dollars = min(per_market_cap, r["buy_dollars"],
                           daily_cap_usd - spent)
        if size_dollars < 5.0:  # Polymarket min order
            plan.append({"row": r, "status": "skip",
                         "reason": f"daily cap reached or order < $5"})
            continue
        size_shares = size_dollars / q.ask
        plan_entry = {
            "row": r, "size_shares": size_shares,
            "size_dollars": size_dollars, "exec_px": q.ask,
        }
        if dry_run:
            plan_entry["status"] = "dry-run"
            plan_entry["order_id"] = None
        else:
            try:
                order = clob.create_order(r["token"], "BUY", size_shares, q.ask)
                resp = clob.post_order(order, "GTC")
                plan_entry["status"] = "submitted"
                plan_entry["order_id"] = resp.get("orderID") or resp.get("id")
                # Record in position book at the *intended* price; reconcile later
                pos.open_position(
                    market_id=str(r["market_id"]),
                    token_id=r["token"], side="BUY",
                    size=size_shares, entry_px=q.ask,
                    fair_at_entry=1.0,
                    edge_bps_at_entry=r["edge_pp"] * 100,
                    validator="tail-decay",
                    market_label=(r["question"] or "")[:80],
                    notes=f"past_deadline={r['past_deadline']}",
                )
                alert(f"FILLED tail-decay {r['side_label']} @ {q.ask:.3f} "
                      f"({size_shares:.0f} shares = ${size_dollars:.0f}) - "
                      f"{(r['question'] or '')[:80]}")
                executions += 1
            except Exception as e:
                plan_entry["status"] = "error"
                plan_entry["reason"] = str(e)
                alert(f"tail-decay order FAILED: {e}")
        plan.append(plan_entry)
        spent += size_dollars
        if spent >= daily_cap_usd:
            break
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-days", type=int, default=7)
    ap.add_argument("--min-ask", type=float, default=0.85)
    ap.add_argument("--max-ask", type=float, default=0.99)
    ap.add_argument("--min-size-usd", type=float, default=50.0)
    ap.add_argument("--per-market-cap", type=float, default=100.0,
                    help="Max $ to spend per single market (default $100)")
    ap.add_argument("--daily-cap", type=float, default=300.0,
                    help="Max $ to spend per scan/run (default $300)")
    ap.add_argument("--min-edge-pp", type=float, default=3.0,
                    help="Min edge in pp to execute (default 3pp after fees)")
    ap.add_argument("--watch", type=int, default=0)
    ap.add_argument("--max-alert", type=int, default=5)
    ap.add_argument("--max-executions", type=int, default=3,
                    help="Max executions per run (red-team rule)")
    ap.add_argument("--execute", action="store_true",
                    help="Execute buys (still dry-run unless --live also set)")
    ap.add_argument("--live", action="store_true",
                    help="ACTUALLY place orders. Requires PM_TRADING_ENABLED=1.")
    ap.add_argument("--allow-past-deadline-only", action="store_true",
                    help="Restrict to past-deadline (DANGEROUS — adverse selected per red-team)")
    ap.add_argument("--skip-objective-check", action="store_true",
                    help="Skip positive resolution-criteria whitelist (NOT recommended)")
    args = ap.parse_args()

    while True:
        try:
            rows = scan(args.max_days, args.min_ask, args.max_ask,
                        args.min_size_usd, args.per_market_cap)
            report(rows, args.max_alert)
            if args.execute:
                plan = execute_buys(
                    rows,
                    per_market_cap=args.per_market_cap,
                    daily_cap_usd=args.daily_cap,
                    min_edge_pp=args.min_edge_pp,
                    only_past_deadline=args.allow_past_deadline_only,
                    require_objective=not args.skip_objective_check,
                    max_executions_per_run=args.max_executions,
                    dry_run=not args.live,
                )
                print(f"\nExecution {'(LIVE)' if args.live else '(dry-run)'}:")
                for p in plan:
                    r = p["row"]
                    print(f"  {p['status']:<10}  {r['side_label']:>4} "
                          f"${p.get('size_dollars',0):>6.0f} @ {p.get('exec_px',0):.3f}  "
                          f"edge {r['edge_pp']:>4.1f}pp  "
                          f"{(r['question'] or '')[:55]}  "
                          f"{p.get('reason','')}")
        except Exception as e:
            sys.stderr.write(f"scan error: {e}\n")
            import traceback; traceback.print_exc()
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
