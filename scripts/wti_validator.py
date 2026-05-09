"""
WTI threshold-market validator.

Compares Polymarket "Will WTI hit $X in May 2026?" prices against
barrier-option fair touch probabilities under GBM, using OVX as
the implied-volatility input.

Free data sources:
  * WTI front-month future: Yahoo Finance CL=F (FRED DCOILWTICO if key set)
  * Implied vol: Yahoo ^OVX (FRED OVXCLS if key set)
  * Polymarket: gamma-api.polymarket.com (no key)

Usage:
    python wti_validator.py
    python wti_validator.py --watch 60
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from scipy.stats import norm

from validator_core import (
    EdgeRow, evaluate_market, format_table, gamma_event,
)

EVENT_SLUG = "what-price-will-wti-hit-in-may-2026"
UA = {"User-Agent": "Mozilla/5.0 (wti-validator)"}
FRED_KEY = os.environ.get("FRED_API_KEY")


# -------------------- math --------------------

def touch_prob(S: float, K: float, T_years: float, sigma_annual: float, mu: float = 0.0) -> float:
    """Probability the GBM path touches barrier K before T.

    Reflection-principle closed form for a driftless / low-drift GBM:
        P(hit) = N(d1) + (S/K)^(1 - 2 mu / sigma^2) * N(d2)
    where d1, d2 use total vol sigma * sqrt(T). Direction inferred from K vs S.
    """
    if T_years <= 0 or sigma_annual <= 0:
        return 1.0 if (K <= S if K < S else K >= S) else 0.0
    sigma = sigma_annual * math.sqrt(T_years)
    if K > S:  # upward barrier
        d1 = (math.log(S / K) + 0.5 * sigma ** 2) / sigma
        d2 = (math.log(S / K) - 0.5 * sigma ** 2) / sigma
        exp = 1 - 2 * mu / sigma_annual ** 2
        return min(norm.cdf(d1) + (S / K) ** exp * norm.cdf(d2), 1.0)
    else:  # downward barrier
        d1 = (math.log(K / S) + 0.5 * sigma ** 2) / sigma
        d2 = (math.log(K / S) - 0.5 * sigma ** 2) / sigma
        exp = 1 - 2 * mu / sigma_annual ** 2
        return min(norm.cdf(d1) + (K / S) ** exp * norm.cdf(d2), 1.0)


# -------------------- data --------------------

def yahoo_last(symbol: str) -> float:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    return float(meta.get("regularMarketPrice") or meta["chartPreviousClose"])


def fred_last(series: str) -> Optional[float]:
    if not FRED_KEY:
        return None
    url = ("https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series}&api_key={FRED_KEY}&file_type=json"
           "&sort_order=desc&limit=5")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        for obs in r.json().get("observations", []):
            v = obs.get("value")
            if v not in (None, ".", ""):
                return float(v)
    except Exception:
        return None
    return None


def get_spot() -> tuple[float, str]:
    f = fred_last("DCOILWTICO")
    if f is not None:
        return f, "FRED DCOILWTICO"
    return yahoo_last("CL=F"), "Yahoo CL=F (front-month future)"


def get_ovx() -> tuple[float, str]:
    f = fred_last("OVXCLS")
    if f is not None:
        return f, "FRED OVXCLS"
    return yahoo_last("^OVX"), "Yahoo ^OVX"


def get_polymarket_event() -> dict:
    ev = gamma_event(EVENT_SLUG)
    if not ev:
        raise RuntimeError(f"event slug not found: {EVENT_SLUG}")
    return ev


# -------------------- parsing --------------------

Q_RE = re.compile(r"\(?(HIGH|LOW)\)?\s*\$?(\d+(?:\.\d+)?)", re.I)


def parse_market(m: dict) -> Optional[dict]:
    q = m.get("question", "")
    match = Q_RE.search(q)
    if not match:
        return None
    direction = "UP" if match.group(1).upper() == "HIGH" else "DOWN"
    strike = float(match.group(2))
    end = m.get("endDate")
    if m.get("closed") or m.get("archived"):
        return None
    return {
        "question": q, "strike": strike, "direction": direction,
        "end": end, "slug": m.get("slug"), "_market": m,
    }


# -------------------- main scan --------------------

def scan_wti_markets(edge_threshold: float = 0.02) -> list[EdgeRow]:
    spot, spot_src = get_spot()
    ovx, ovx_src = get_ovx()
    sigma = ovx / 100.0

    event = get_polymarket_event()
    parsed = [r for r in (parse_market(m) for m in event.get("markets", [])) if r]
    if not parsed:
        print("no rows parsed from event"); return []

    now = datetime.now(timezone.utc)
    end = max(datetime.fromisoformat(r["end"].replace("Z", "+00:00")) for r in parsed)
    days = max((end - now).total_seconds() / 86400.0, 0.0)
    T = days / 365.0

    print(f"\nAs of {now.isoformat(timespec='seconds')}")
    print(f"Spot WTI : ${spot:6.2f}  ({spot_src})")
    print(f"OVX      :  {ovx:5.2f}   sigma={sigma:.3f}  ({ovx_src})")
    print(f"Days to expiry: {days:.2f}\n")

    rows: list[EdgeRow] = []
    for r in sorted(parsed, key=lambda x: (x["direction"], x["strike"])):
        fair = touch_prob(spot, r["strike"], T, sigma)
        # Try buy side first; if PM looks rich vs fair, evaluate as sell.
        side = "buy"
        row = evaluate_market(
            r["_market"], fair=fair, side=side,
            threshold_bps=int(edge_threshold * 10000),
            validator_name="wti",
            market_label=f"WTI {r['direction']} ${r['strike']:.0f} ({days:.0f}d)",
        )
        if row.edge_bps is not None and row.edge_bps < 0:
            row = evaluate_market(
                r["_market"], fair=fair, side="sell",
                threshold_bps=int(edge_threshold * 10000),
                validator_name="wti",
                market_label=f"WTI {r['direction']} ${r['strike']:.0f} ({days:.0f}d)",
            )
        if row.pm_yes is None or row.pm_yes >= 0.999 or row.pm_yes <= 0.001:
            continue
        rows.append(row)

    rows.sort(key=lambda x: -abs(x.edge_bps or 0))
    print(format_table(rows, title="wti_validator (CLOB-aware)"))
    flagged = [r for r in rows if r.edge_bps and abs(r.edge_bps) >= edge_threshold * 10000 and not r.skipped]
    skipped = [r for r in rows if r.skipped]
    print(f"{len(flagged)} actionable / {len(rows)} markets.  ({len(skipped)} skipped for liquidity/spread.)")
    if skipped:
        print("\nSkipped (would-be edges, but unexecutable):")
        for r in skipped[:5]:
            print(f"  {r.market[:50]:<50}  edge={(r.edge_bps or 0)/100:+.1f}pp   {r.skip_reason}")
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--watch", type=int, default=0,
                   help="re-run every N seconds (0 = once)")
    p.add_argument("--edge", type=float, default=0.02,
                   help="edge threshold for flagging (default 0.02 = 2pp)")
    args = p.parse_args()

    while True:
        try:
            scan_wti_markets(args.edge)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
