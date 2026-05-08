"""Eurovision 2026 Winner — Polymarket vs market reference (eurovisionworld aggregator).

Polymarket: Gamma API, slug "eurovision-winner-2026" (~$136M volume; 41 named country
markets + 9 placeholders).

Market reference: eurovisionworld.com/odds/eurovision aggregates Bet365 / Betfair /
Unibet / William Hill / etc. Betfair Exchange direct scraping is geofenced/blocked
without auth; Oddschecker returns 403 to bots. The aggregator IS our proxy for the
"true market" — it averages multiple books, which absorbs the Betfair Exchange signal
plus retail-book bias, and is the same source most pundits cite.

Usage:
    python eurovision_validator.py            # one-shot
    python eurovision_validator.py --watch 60 # refresh every 60s
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from typing import Dict

PM_SLUG = "eurovision-winner-2026"
PM_URL = f"https://gamma-api.polymarket.com/events?slug={PM_SLUG}"
EVW_URL = "https://eurovisionworld.com/odds/eurovision"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_polymarket() -> Dict[str, float]:
    """Return {country: yes_price} for named country markets only."""
    data = json.loads(_get(PM_URL))
    if not data:
        raise RuntimeError(f"Polymarket: empty response for slug {PM_SLUG}")
    event = data[0]
    out: Dict[str, float] = {}
    pat = re.compile(r"^Will (.+?) win Eurovision 2026\?$")
    for m in event.get("markets", []):
        q = m.get("question", "")
        mt = pat.match(q)
        if not mt:
            continue
        country = mt.group(1).strip()
        if country.lower().startswith("country ") or country.lower().startswith("another"):
            continue  # placeholders
        op = m.get("outcomePrices")
        if not op:
            continue
        try:
            prices = json.loads(op) if isinstance(op, str) else op
            yes = float(prices[0])
        except (ValueError, TypeError, IndexError):
            continue
        if yes <= 0:
            continue
        out[country] = yes
    return out


def fetch_eurovisionworld() -> Dict[str, float]:
    """Scrape the aggregator. Returns {country: implied_prob} from average decimal odds."""
    html = _get(EVW_URL)
    # Rows look like: <td>Finland</td>...<td class="odds_avg">2.28</td>
    # Defensive multi-pattern: country name in <a> or <td>, average odds nearby.
    out: Dict[str, float] = {}
    # Try structured: each odds row contains a country anchor and an average column.
    # Pattern: country link followed (within ~2KB) by data-avg or "odds_avg".
    row_re = re.compile(
        r'href="/[^"]*"[^>]*>\s*<[^>]+>\s*</[^>]+>\s*([A-Z][A-Za-z .\-]+?)\s*</a>.*?'
        r'(?:data-avg="|odds_avg[^>]*>)\s*([0-9]+\.?[0-9]*)',
        re.DOTALL,
    )
    for country, odds in row_re.findall(html):
        try:
            d = float(odds)
        except ValueError:
            continue
        if d <= 1.01:
            continue
        c = country.strip()
        if c and c not in out:
            out[c] = 1.0 / d
    if not out:
        # Fallback: previously verified WebFetch values from 8 May 2026.
        # Only used if scraping fails — flagged in output.
        fallback = {
            "Finland": 2.28, "Greece": 5.70, "Denmark": 6.85, "France": 11.40,
            "Australia": 12.30, "Israel": 17.70, "Italy": 20.60, "Romania": 23.40,
            "Malta": 24.60, "Sweden": 34.90, "Ukraine": 45.70, "Cyprus": 65.60,
            "Croatia": 89.10, "Czechia": 97.90, "Bulgaria": 101.30, "Moldova": 102.20,
            "Luxembourg": 111.60, "Norway": 145.70, "United Kingdom": 157.80,
            "Lithuania": 167.70, "Albania": 169.90, "Serbia": 214.50, "Germany": 226.00,
            "Latvia": 280.50, "Switzerland": 290.70, "Armenia": 331.00, "Poland": 367.80,
            "Austria": 401.0, "Belgium": 401.0, "Georgia": 401.0, "San Marino": 401.0,
            "Portugal": 401.0, "Estonia": 401.0, "Montenegro": 401.0, "Azerbaijan": 401.0,
        }
        out = {k: 1.0 / v for k, v in fallback.items()}
        out["__fallback__"] = -1.0  # sentinel
    return out


def devig(prices: Dict[str, float]) -> Dict[str, float]:
    clean = {k: v for k, v in prices.items() if not k.startswith("__")}
    total = sum(clean.values())
    if total <= 0:
        return clean
    return {k: v / total for k, v in clean.items()}


def edge_pp(pm_dv: Dict[str, float], mk_dv: Dict[str, float]) -> Dict[str, float]:
    return {c: (pm_dv.get(c, 0.0) - mk_dv.get(c, 0.0)) * 100 for c in mk_dv}


# Eurovisionworld sometimes renders "United Kingdom" while PM uses same; normalize just in case.
ALIAS = {"UK": "United Kingdom", "Czech Republic": "Czechia"}


def normalize(d: Dict[str, float]) -> Dict[str, float]:
    return {ALIAS.get(k, k): v for k, v in d.items()}


def scan_eurovision() -> None:
    print(f"\n=== Eurovision 2026 Winner — PM vs Market Aggregator ===")
    print(f"PM slug: {PM_SLUG}")
    try:
        pm_raw = normalize(fetch_polymarket())
    except Exception as e:
        print(f"Polymarket fetch FAILED: {e}", file=sys.stderr)
        return
    try:
        mk_raw_full = fetch_eurovisionworld()
    except Exception as e:
        print(f"Aggregator fetch FAILED: {e}", file=sys.stderr)
        return
    fallback = mk_raw_full.pop("__fallback__", None) is not None
    mk_raw = normalize(mk_raw_full)

    pm_overround = sum(pm_raw.values())
    mk_overround = sum(mk_raw.values())
    pm_dv = devig(pm_raw)
    mk_dv = devig(mk_raw)
    edges = edge_pp(pm_dv, mk_dv)

    intersect = sorted(set(pm_dv) & set(mk_dv), key=lambda c: -mk_dv[c])
    only_pm = sorted(set(pm_dv) - set(mk_dv))
    only_mk = sorted(set(mk_dv) - set(pm_dv))

    src_note = "FALLBACK (cached 8 May 2026)" if fallback else "live scrape"
    print(f"Source — Polymarket: live  |  Aggregator: {src_note}")
    print(f"Overround — PM raw sum: {pm_overround:.3f}  |  Aggregator implied sum: {mk_overround:.3f}")
    print()
    print(f"{'Country':<18}{'PM':>7}{'Mkt':>8}{'Edge':>8}  Action")
    print("-" * 70)
    for c in intersect:
        pm = pm_dv[c] * 100
        mk = mk_dv[c] * 100
        e = edges[c]
        if abs(e) < 2.0:
            action = "."
        elif e > 0:
            action = f"** PM rich  ->  SELL YES  ({e:+.1f}pp)"
        else:
            action = f"** PM cheap ->  BUY  YES  ({e:+.1f}pp)"
        print(f"{c:<18}{pm:>6.1f}%{mk:>7.1f}%{e:>+7.1f}pp  {action}")
    if only_pm:
        print(f"\nOnly on Polymarket: {', '.join(only_pm)}")
    if only_mk:
        print(f"Only on aggregator: {', '.join(only_mk)}")
    flagged = [c for c in intersect if abs(edges[c]) >= 2.0]
    print(f"\n{len(flagged)} edge(s) >= 2pp flagged.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="refresh interval in seconds (0 = one-shot)")
    args = ap.parse_args()
    if args.watch <= 0:
        scan_eurovision()
        return
    while True:
        scan_eurovision()
        print(f"\n[sleep {args.watch}s — Ctrl-C to exit]")
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
