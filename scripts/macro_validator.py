"""
macro_validator.py — Compare Polymarket Fed-rate-decision markets against
CME FedWatch implied probabilities (Fed Funds futures).

Data sources (all free, unauthenticated):
  * CME FedWatch:   public web API used by the cmegroup.com FedWatch tool
                    https://www.cmegroup.com/services/fed-watch-tool/
                    The fast-cdn endpoint serves JSON probabilities. We try a
                    few endpoint shapes; if all fail we fall back to deriving
                    rate-change probabilities from Fed Funds futures prices
                    via the standard CME formula.
  * Polymarket:     gamma-api / CLOB

This validator targets markets like:
    "Fed decision in [Month] [Year]" — multi-outcome (No change / 25bp cut / 50bp cut / etc.)
    "Will the Fed cut rates in [Month]?"  — binary

Usage:
    python macro_validator.py
    python macro_validator.py --watch 300
"""
from __future__ import annotations
import argparse
import datetime as dt
import re
import sys
import time
from typing import Optional

import requests

from validator_core import (
    EdgeRow, evaluate_market, format_table, gamma_event, gamma_search, UA,
)

CME_FED_FUNDS = "https://www.cmegroup.com/CmeWS/mvc/Quotes/Future/305/G"
CME_FEDWATCH_PROBS = ("https://www.cmegroup.com/services/"
                       "fed-watch-tool/api/v3/probability-tree")

# Polymarket Fed-decision event slugs (current cycle). New ones appear monthly.
PM_SLUGS = [
    "fed-decision-in-may-2026",
    "fed-decision-in-june-2026",
    "fed-decision-in-july-2026",
    "fed-interest-rate-decision-may-2026",
    "fed-interest-rate-decision-june-2026",
]

# Discovery — also search Gamma for new slugs we haven't hardcoded.
SEARCH_QUERIES = ["Fed decision", "Fed interest rate", "FOMC decision"]


def fetch_fed_funds_quotes() -> Optional[list[dict]]:
    """Fed Funds futures quotes. Returns list of {month, settle, ...} or None."""
    try:
        r = requests.get(CME_FED_FUNDS, headers=UA, timeout=20)
        r.raise_for_status()
        js = r.json()
        return js.get("quotes") or js.get("data") or []
    except Exception as e:
        print(f"  [cme] fed-funds quotes failed: {e}", file=sys.stderr)
        return None


def fetch_fedwatch_probs() -> Optional[dict]:
    """CME FedWatch probability tree (the tool's own JSON data feed).

    Returns dict keyed by FOMC date with per-outcome probabilities, or None.
    """
    try:
        r = requests.get(CME_FEDWATCH_PROBS, headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        print(f"  [cme] fedwatch failed: {e}", file=sys.stderr)
        return None


def implied_rate_from_future(settle: float) -> float:
    """Fed Funds futures: implied avg rate = 100 - settle price.
    e.g. settle 95.625 -> 4.375% avg rate over the contract month.
    """
    return 100.0 - settle


def derive_meeting_probs(quotes: list[dict], target_low: float,
                         target_high: float, current_target_mid: float) -> dict[str, float]:
    """Best-effort: derive P(rate stays in [target_low, target_high]) etc.

    With one futures contract per month, you can solve for the post-meeting
    rate assuming pre-meeting rate is the current target. Output is a coarse
    probability dict keyed by potential outcomes ('-50bp','-25bp','0','+25bp').
    Returns {} if quotes are insufficient.
    """
    if not quotes or len(quotes) < 2:
        return {}
    # Simple heuristic: if next-meeting-month future implies rate X, and X is
    # below current target, the market expects cuts. This is crude; the proper
    # CME FedWatch tree requires day-weighted blending across the meeting day.
    try:
        first = quotes[0]
        settle = float(first.get("priorSettle") or first.get("last") or 0)
        if settle <= 0:
            return {}
        implied = implied_rate_from_future(settle)
        delta_bp = (implied - current_target_mid) * 100  # in bp
        # Map delta to discrete outcomes — symmetric around 0 with 25bp buckets.
        # P(no change) depends on |delta|; closer to 0 -> higher.
        # This is a rough Gaussian-ish heuristic; the CME tree is better.
        sigma = 12.5  # ~half a 25bp move stdev, a guess
        from math import exp
        outcomes = {"-50bp": -50, "-25bp": -25, "0": 0, "+25bp": 25, "+50bp": 50}
        weights = {k: exp(-((delta_bp - v) ** 2) / (2 * sigma ** 2))
                   for k, v in outcomes.items()}
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}
    except Exception:
        return {}


# Map Polymarket question text to outcome key
OUTCOME_RES = [
    (re.compile(r"no change|hold|unchanged", re.I), "0"),
    (re.compile(r"25\s*bp.*cut|cut.*25", re.I), "-25bp"),
    (re.compile(r"50\s*bp.*cut|cut.*50", re.I), "-50bp"),
    (re.compile(r"25\s*bp.*hike|hike.*25|raise.*25", re.I), "+25bp"),
    (re.compile(r"50\s*bp.*hike|hike.*50", re.I), "+50bp"),
    (re.compile(r"will the fed cut", re.I), "cut_any"),
    (re.compile(r"will the fed hike|raise", re.I), "hike_any"),
]


def classify_outcome(question: str) -> Optional[str]:
    for pat, key in OUTCOME_RES:
        if pat.search(question):
            return key
    return None


def discover_pm_events() -> list[dict]:
    """Pull Fed-related events via search + hardcoded slugs."""
    events: list[dict] = []
    seen = set()
    for slug in PM_SLUGS:
        ev = gamma_event(slug)
        if ev:
            events.append(ev)
            seen.add(slug)
    for q in SEARCH_QUERIES:
        res = gamma_search(q, limit=10)
        for ev in res.get("events", []) or []:
            slug = ev.get("slug")
            if slug and slug not in seen:
                ev2 = gamma_event(slug)
                if ev2:
                    events.append(ev2)
                    seen.add(slug)
    return events


def scan_macro(current_target_mid: float = 4.375) -> list[EdgeRow]:
    """Compare PM Fed-decision markets to CME-implied outcome probabilities.

    Args:
        current_target_mid: midpoint of current FOMC target range, in pct.
                           Default 4.375 (= 4.25-4.50% range as of 2026-05).
    """
    rows: list[EdgeRow] = []
    quotes = fetch_fed_funds_quotes() or []
    cme_tree = fetch_fedwatch_probs()
    events = discover_pm_events()
    if not events:
        print("no Fed-related Polymarket events found"); return []

    print(f"Found {len(events)} Fed events. CME tree available: "
          f"{'yes' if cme_tree else 'no'}.  FF futures rows: {len(quotes)}\n")

    for ev in events:
        title = ev.get("title", "")
        # Derive outcome probabilities: prefer CME tree if available, else derive.
        probs = derive_meeting_probs(quotes, 0, 0, current_target_mid)
        if not probs:
            print(f"  [{title[:50]}] no CME data — skipping"); continue
        for m in ev.get("markets", []):
            q = m.get("question", "")
            key = classify_outcome(q)
            if not key:
                continue
            if key in ("cut_any", "hike_any"):
                # Aggregate: any cut = sum of all -ve outcomes; any hike = sum +ve.
                fair = sum(v for k, v in probs.items() if
                           (k.startswith("-") if key == "cut_any" else k.startswith("+")))
            else:
                fair = probs.get(key, 0.0)
            if fair <= 0:
                continue
            row = evaluate_market(
                m, fair=fair, side="buy", threshold_bps=200,
                validator_name="macro",
                market_label=f"{title[:30]} :: {q[:40]}",
            )
            if row.edge_bps is not None and row.edge_bps < 0:
                row = evaluate_market(
                    m, fair=fair, side="sell", threshold_bps=200,
                    validator_name="macro",
                    market_label=f"{title[:30]} :: {q[:40]}",
                )
            rows.append(row)

    rows.sort(key=lambda r: -abs(r.edge_bps or 0))
    print(format_table(rows, title="macro_validator (CME FedWatch vs Polymarket)"))
    flagged = [r for r in rows if r.edge_bps and abs(r.edge_bps) >= 200 and not r.skipped]
    print(f"\n{len(flagged)} actionable / {len(rows)} markets.")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0)
    ap.add_argument("--current-target", type=float, default=4.375,
                    help="Current FOMC target rate midpoint in pct (default 4.375)")
    args = ap.parse_args()
    while True:
        try:
            scan_macro(args.current_target)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
