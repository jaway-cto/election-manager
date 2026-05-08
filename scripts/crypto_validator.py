"""
crypto_validator.py — Flag arbitrage between Polymarket BTC threshold markets
and Deribit-implied fair-value touch probabilities.

Spot + IV from Deribit (free, no auth). Polymarket prices from Gamma API.
Reflection-principle GBM touch probability with no drift.
"""
from __future__ import annotations
import argparse
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

try:
    from scipy.stats import norm
    _ncdf = norm.cdf
except ImportError:
    def _ncdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

DERIBIT = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
BINANCE = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
GAMMA_EVENT = "https://gamma-api.polymarket.com/events?slug={slug}"

# Threshold-event slugs to monitor (BTC only — IV pulled from BTC chain)
EVENT_SLUGS = [
    "what-price-will-bitcoin-hit-in-may-2026",
    "when-will-bitcoin-hit-150k",
]

INSTRUMENT_RE = re.compile(r"^BTC-(\d{1,2})([A-Z]{3})(\d{2})-(\d+)-([CP])$")
MONTHS = {m: i for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], start=1)}


@dataclass
class Option:
    expiry: datetime
    strike: float
    iv: float  # decimal annual


def fair_touch(spot: float, strike: float, days: float, iv_annual: float) -> float:
    """Reflection-principle one-touch probability under GBM, zero drift."""
    if days <= 0 or iv_annual <= 0:
        return 1.0 if (strike <= spot) == (strike <= spot) else 0.0
    T = days / 365.0
    sigma = iv_annual * math.sqrt(T)
    if strike > spot:
        d2 = math.log(spot / strike) / sigma - 0.5 * sigma
        finish = _ncdf(d2)
    else:
        d2 = math.log(spot / strike) / sigma + 0.5 * sigma
        finish = 1.0 - _ncdf(d2)
    return min(2.0 * finish, 1.0)


def fetch_deribit_chain() -> tuple[float, list[Option]]:
    r = requests.get(DERIBIT, params={"currency": "BTC", "kind": "option"}, timeout=15)
    r.raise_for_status()
    data = r.json()["result"]
    spot = float(data[0]["underlying_price"])
    opts: list[Option] = []
    for row in data:
        m = INSTRUMENT_RE.match(row["instrument_name"])
        iv = row.get("mark_iv")
        if not m or iv is None:
            continue
        d, mon, yy, strike, _ = m.groups()
        try:
            exp = datetime(2000 + int(yy), MONTHS[mon], int(d), 8, 0, tzinfo=timezone.utc)
        except KeyError:
            continue
        opts.append(Option(exp, float(strike), float(iv) / 100.0))
    return spot, opts


def fetch_binance_spot() -> Optional[float]:
    try:
        r = requests.get(BINANCE, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return None


def atm_iv(opts: list[Option], spot: float, target: datetime) -> Optional[float]:
    """Average IV of the 4 options closest in (log-strike, expiry) to (spot, target)."""
    if not opts:
        return None
    scored = []
    for o in opts:
        dt_days = abs((o.expiry - target).days)
        moneyness = abs(math.log(o.strike / spot))
        scored.append((dt_days * 0.05 + moneyness, o.iv))
    scored.sort(key=lambda x: x[0])
    top = [iv for _, iv in scored[:6]]
    return sum(top) / len(top) if top else None


def fetch_event(slug: str) -> list[dict]:
    r = requests.get(GAMMA_EVENT.format(slug=slug), timeout=15)
    r.raise_for_status()
    js = r.json()
    if not js:
        return []
    ev = js[0] if isinstance(js, list) else js
    return ev.get("markets", [])


# Parse "Will Bitcoin reach $120,000 in May?" / "dip to $75,000" / "hit $150k by ..."
THRESH_RE = re.compile(
    r"(reach|hit|dip to|above)\s*\$?([\d,]+)k?",
    re.IGNORECASE,
)


def parse_market(m: dict) -> Optional[dict]:
    q = m.get("question", "")
    mm = THRESH_RE.search(q)
    if not mm:
        return None
    verb, num = mm.group(1).lower(), mm.group(2).replace(",", "")
    raw = float(num)
    # heuristic: 'k' suffix or value < 1000 => thousands
    if "k" in q.lower().split(num)[-1][:2] or raw < 1000:
        strike = raw * 1000
    else:
        strike = raw
    end_iso = m.get("endDate") or m.get("end_date_iso")
    if not end_iso:
        return None
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    prices = m.get("outcomePrices")
    if isinstance(prices, str):
        import json
        prices = json.loads(prices)
    if not prices:
        return None
    yes = float(prices[0])
    direction = "down" if verb == "dip to" else "up"
    return {
        "question": q,
        "strike": strike,
        "end": end,
        "yes": yes,
        "direction": direction,
        "active": m.get("active", True) and not m.get("closed", False),
    }


def scan_crypto_markets() -> list[dict]:
    spot, opts = fetch_deribit_chain()
    binance = fetch_binance_spot()
    now = datetime.now(timezone.utc)
    rows: list[dict] = []

    for slug in EVENT_SLUGS:
        try:
            markets = fetch_event(slug)
        except Exception as e:
            print(f"  ! fetch {slug}: {e}", file=sys.stderr)
            continue
        for m in markets:
            p = parse_market(m)
            if not p or not p["active"]:
                continue
            days = (p["end"] - now).total_seconds() / 86400.0
            if days <= 0:
                continue
            # Skip degenerate already-touched markets (YES=1 or 0)
            if p["yes"] >= 0.999 or p["yes"] <= 0.001:
                continue
            iv = atm_iv(opts, spot, p["end"])
            if iv is None:
                continue
            # 'dip to' = touch below; 'reach/hit' = touch above
            if p["direction"] == "down" and p["strike"] >= spot:
                continue
            if p["direction"] == "up" and p["strike"] <= spot:
                continue
            fair = fair_touch(spot, p["strike"], days, iv)
            edge = fair - p["yes"]
            action = "-"
            if abs(edge) > 0.02:
                action = "BUY YES" if edge > 0 else "BUY NO"
            rows.append({
                "q": p["question"][:34],
                "strike": p["strike"],
                "days": days,
                "spot": spot,
                "iv": iv,
                "yes": p["yes"],
                "fair": fair,
                "edge": edge,
                "action": action,
            })

    print(f"\nDeribit spot: ${spot:,.0f}   Binance: "
          f"{'$%.0f' % binance if binance else 'n/a'}   "
          f"{now:%Y-%m-%d %H:%M UTC}\n")
    hdr = f"{'Market':<36}{'Strike':>8}{'Days':>6}{'IV':>7}{'PM':>8}{'Fair':>8}{'Edge':>8}  Action"
    print(hdr)
    print("-" * len(hdr))
    rows.sort(key=lambda r: -abs(r["edge"]))
    for r in rows:
        print(f"{r['q']:<36}{r['strike']:>8.0f}{r['days']:>6.0f}"
              f"{r['iv']:>7.2f}{r['yes']*100:>7.1f}%{r['fair']*100:>7.1f}%"
              f"{r['edge']*100:>+7.1f}pp  {r['action']}")
    flagged = [r for r in rows if abs(r["edge"]) > 0.02]
    print(f"\n{len(flagged)} flagged of {len(rows)} markets (|edge| > 2pp).")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="Poll every 60s")
    args = ap.parse_args()
    if args.watch:
        while True:
            try:
                scan_crypto_markets()
            except Exception as e:
                print(f"error: {e}", file=sys.stderr)
            time.sleep(60)
    else:
        scan_crypto_markets()


if __name__ == "__main__":
    main()
