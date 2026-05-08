"""Cross-venue scanner: Polymarket vs Kalshi on 2026/2028 political markets.

Surfaces gaps > 3pp as actionable arbitrage candidates and runs an internal
consistency check on Polymarket (sum of candidate-as-president markets vs
party-wins-presidency market).

Verified endpoints (no auth):
  - Kalshi:     https://api.elections.kalshi.com/trade-api/v2/markets
  - Polymarket: https://gamma-api.polymarket.com/events

Discovered Kalshi series tickers used:
  - KXPRESPARTY  -> KXPRESPARTY-2028-D, KXPRESPARTY-2028-R
                   (party-wins-2028; markets exist but illiquid as of run date)
  - KXHOUSE      -> US House control (currently no open 2026 sub-markets)

Polymarket slugs used:
  - presidential-election-winner-2028
  - which-party-wins-2028-us-presidential-election
  - democratic-presidential-nominee-2028
  - republican-presidential-nominee-2028
  - which-party-will-win-the-house-in-2026
"""
from __future__ import annotations

import json
import sys
from typing import Any
from urllib.parse import urlencode

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY = "https://gamma-api.polymarket.com"
ARB_THRESHOLD_PP = 3.0
TIMEOUT = 20


# ---------------------------- fetchers ----------------------------

def kalshi_markets(series_ticker: str) -> list[dict[str, Any]]:
    r = requests.get(f"{KALSHI}/markets?{urlencode({'series_ticker': series_ticker, 'limit': 200})}",
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("markets", [])


def poly_event(slug: str) -> dict[str, Any] | None:
    r = requests.get(f"{POLY}/events?slug={slug}", timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data[0] if data else None


# ------------------------- price extraction -------------------------

def kalshi_yes_prob(m: dict[str, Any]) -> float | None:
    """Kalshi quotes cents 0-100. Use mid of bid/ask, fall back to last_price."""
    bid, ask = m.get("yes_bid"), m.get("yes_ask")
    if bid is not None and ask is not None:
        return (bid + ask) / 2 / 100
    last = m.get("last_price")
    if last is not None:
        return last / 100
    return None


def poly_yes_prob(m: dict[str, Any]) -> float | None:
    """Polymarket outcomePrices is JSON-encoded ['yes','no']."""
    op = m.get("outcomePrices")
    if not op:
        return None
    try:
        prices = json.loads(op) if isinstance(op, str) else op
        return float(prices[0])
    except (ValueError, IndexError, TypeError):
        return None


# ------------------------- analysis primitives -------------------------

def fmt_pct(p: float | None) -> str:
    return "  -  " if p is None else f"{p * 100:5.1f}%"


def action(pm: float | None, ks: float | None) -> tuple[str, float | None]:
    if pm is None or ks is None:
        return ("no Kalshi" if ks is None else "no PM"), None
    diff_pp = (pm - ks) * 100
    if abs(diff_pp) >= ARB_THRESHOLD_PP:
        side = "buy Kalshi YES / sell PM" if diff_pp > 0 else "buy PM YES / sell Kalshi"
        return f"ARB {diff_pp:+.1f}pp -> {side}", diff_pp
    return "no edge", diff_pp


def row(label: str, pm: float | None, ks: float | None) -> dict[str, Any]:
    act, diff = action(pm, ks)
    return {"label": label, "pm": pm, "ks": ks, "diff_pp": diff, "action": act}


# ------------------------- scanner -------------------------

def scan_cross_venue() -> None:
    rows: list[dict[str, Any]] = []
    notes: list[str] = []

    # --- Kalshi 2028 party ---
    kp = {m["ticker"]: m for m in kalshi_markets("KXPRESPARTY")}
    ks_dem = kalshi_yes_prob(kp["KXPRESPARTY-2028-D"]) if "KXPRESPARTY-2028-D" in kp else None
    ks_gop = kalshi_yes_prob(kp["KXPRESPARTY-2028-R"]) if "KXPRESPARTY-2028-R" in kp else None
    if ks_dem is None and "KXPRESPARTY-2028-D" in kp:
        notes.append("Kalshi KXPRESPARTY-2028-D exists but has no bid/ask/last (illiquid).")
    if ks_gop is None and "KXPRESPARTY-2028-R" in kp:
        notes.append("Kalshi KXPRESPARTY-2028-R exists but has no bid/ask/last (illiquid).")

    # --- Polymarket: party-wins-2028 (single binary-per-side event) ---
    pm_party = poly_event("which-party-wins-2028-us-presidential-election")
    pm_dem_party = pm_gop_party = None
    if pm_party:
        for m in pm_party.get("markets", []):
            q = (m.get("question") or "").lower()
            p = poly_yes_prob(m)
            if "democratic" in q or "democrat " in q or q.startswith("will the democrat"):
                pm_dem_party = p
            elif "republican" in q:
                pm_gop_party = p

    # --- Polymarket: candidate-as-president (winner) markets ---
    pm_winner = poly_event("presidential-election-winner-2028")
    winner_markets: list[tuple[str, float]] = []
    if pm_winner:
        for m in pm_winner.get("markets", []):
            p = poly_yes_prob(m)
            vol = m.get("volume") or 0
            try:
                vol = float(vol)
            except (TypeError, ValueError):
                vol = 0
            if p is None or vol <= 0:
                continue
            q = m.get("question", "")
            name = q.replace("Will ", "").replace(" win the 2028 US Presidential Election?", "").strip()
            winner_markets.append((name, p))
    winner_markets.sort(key=lambda x: -x[1])
    pm_winner_sum = sum(p for _, p in winner_markets) if winner_markets else None

    # --- Polymarket: party-nominee events (candidate-level) ---
    def nominee_breakdown(slug: str) -> tuple[list[tuple[str, float]], float | None, bool]:
        """Returns (candidates, yes_sum, neg_risk_flag).

        For negRisk events, yes_sum < 100% is expected — the missing probability
        is the implicit 'Other' (an unlisted candidate winning the nomination).
        """
        ev = poly_event(slug)
        if not ev:
            return [], None, False
        neg_risk = bool(ev.get("negRisk") or ev.get("enableNegRisk"))
        out: list[tuple[str, float]] = []
        for m in ev.get("markets", []):
            # Filter resolved/closed/archived markets — they distort sums
            if m.get("closed") or m.get("archived"):
                continue
            if m.get("active") is False:
                continue
            p = poly_yes_prob(m)
            if p is None:
                continue
            q = m.get("question", "")
            name = q.replace("Will ", "").split(" be the ")[0].strip()
            out.append((name, p))
        out.sort(key=lambda x: -x[1])
        return out, sum(p for _, p in out), neg_risk

    dem_nom, dem_nom_sum, dem_neg_risk = nominee_breakdown("democratic-presidential-nominee-2028")
    gop_nom, gop_nom_sum, gop_neg_risk = nominee_breakdown("republican-presidential-nominee-2028")

    # --- 2026 House ---
    pm_house = poly_event("which-party-will-win-the-house-in-2026")
    pm_house_dem = pm_house_gop = None
    if pm_house:
        for m in pm_house.get("markets", []):
            q = (m.get("question") or "").lower()
            p = poly_yes_prob(m)
            if "democrat" in q:
                pm_house_dem = p
            elif "republican" in q:
                pm_house_gop = p
    # Kalshi KXHOUSE: probe for a 2026 sub-market
    ks_house_dem = ks_house_gop = None
    for m in kalshi_markets("KXHOUSE"):
        t = m.get("ticker", "")
        if "2026" not in t:
            continue
        p = kalshi_yes_prob(m)
        if t.endswith("-D"):
            ks_house_dem = p
        elif t.endswith("-R"):
            ks_house_gop = p

    # --- assemble rows ---
    rows.append(row("Dem party 2028 (PM single mkt)", pm_dem_party, ks_dem))
    rows.append(row("GOP party 2028 (PM single mkt)", pm_gop_party, ks_gop))
    rows.append(row("Dem party 2028 (sum-of-cands)", None, ks_dem))  # placeholder, filled below
    rows.append(row("GOP party 2028 (sum-of-cands)", None, ks_gop))
    rows.append(row("Dem House 2026", pm_house_dem, ks_house_dem))
    rows.append(row("GOP House 2026", pm_house_gop, ks_house_gop))

    # --- print headline table ---
    print(f"{'Market':<38}{'PM':>8}{'Kalshi':>10}{'d pp':>9}  Action")
    print("-" * 95)
    for r in rows:
        d = "" if r["diff_pp"] is None else f"{r['diff_pp']:+5.1f}"
        print(f"{r['label']:<38}{fmt_pct(r['pm']):>8}{fmt_pct(r['ks']):>10}{d:>9}  {r['action']}")

    # --- candidate breakdowns ---
    print("\nPolymarket Dem nominee top 8:")
    for n, p in dem_nom[:8]:
        print(f"  {n:<35}{fmt_pct(p)}")
    print(f"  {'SUM':<35}{fmt_pct(dem_nom_sum)}")

    print("\nPolymarket GOP nominee top 8:")
    for n, p in gop_nom[:8]:
        print(f"  {n:<35}{fmt_pct(p)}")
    print(f"  {'SUM':<35}{fmt_pct(gop_nom_sum)}")

    print("\nPolymarket 'who wins the 2028 election' top 10 traded:")
    for n, p in winner_markets[:10]:
        print(f"  {n:<35}{fmt_pct(p)}")
    print(f"  {'SUM (all traded)':<35}{fmt_pct(pm_winner_sum)}")

    # --- internal consistency on Polymarket ---
    print("\nInternal Polymarket consistency:")
    if pm_dem_party is not None and pm_gop_party is not None:
        s = pm_dem_party + pm_gop_party
        print(f"  Dem-party + GOP-party = {s * 100:.1f}% (expect ~100; deviation = {(s - 1) * 100:+.1f}pp)")
    if pm_winner_sum is not None:
        print(f"  Sum of candidate-wins-presidency = {pm_winner_sum * 100:.1f}% "
              f"(expect ~100; deviation = {(pm_winner_sum - 1) * 100:+.1f}pp)")
        if pm_dem_party is not None and pm_gop_party is not None:
            party_total = pm_dem_party + pm_gop_party
            mismatch = (pm_winner_sum - party_total) * 100
            flag = " <-- MISMATCH" if abs(mismatch) >= ARB_THRESHOLD_PP else ""
            print(f"  Cand-sum vs party-sum gap = {mismatch:+.1f}pp{flag}")
    def explain_nom_sum(party: str, total: float | None, neg_risk: bool) -> None:
        if total is None:
            return
        pct = total * 100
        if neg_risk:
            implicit_other = (1.0 - total) * 100
            verdict = "expected for negRisk event"
            if total > 1.05:
                verdict = "OVERROUND vs negRisk: sell-basket arb candidate"
            elif implicit_other > 50:
                verdict = "implicit Other > 50% — listed slate likely missing frontrunner"
            print(f"  {party} nominee sum = {pct:.1f}% (negRisk; implicit Other "
                  f"= {implicit_other:+.1f}pp). {verdict}.")
        else:
            dev = (total - 1) * 100
            flag = " <-- INCONSISTENT" if abs(dev) > 5 else ""
            print(f"  {party} nominee sum = {pct:.1f}% (non-negRisk; deviation "
                  f"{dev:+.1f}pp){flag}.")

    explain_nom_sum("Dem", dem_nom_sum, dem_neg_risk)
    explain_nom_sum("GOP", gop_nom_sum, gop_neg_risk)

    if notes:
        print("\nNotes:")
        for n in notes:
            print(f"  - {n}")


if __name__ == "__main__":
    try:
        scan_cross_venue()
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
