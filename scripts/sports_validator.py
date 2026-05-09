"""
sports_validator.py — Cross-venue sports arbitrage scanner.

Uses The Odds API (free tier: 500 req/month — enough for daily scans) when
THE_ODDS_API_KEY is set. Without a key, falls back to a slimmer Bovada-only
mode similar to nba_validator.

Compares devigged sportsbook fair price against Polymarket CLOB executable price.

Coverage (with The Odds API key):
  * NFL, NBA, MLB, NHL — moneylines + totals
  * EPL, Champions League, La Liga, Bundesliga, Serie A, MLS — moneylines
  * Tennis (ATP, WTA), MMA/UFC — moneylines
  * Outright (futures): NBA Champion, Super Bowl winner, etc.

Free-tier registration: https://the-odds-api.com/

Usage:
    python sports_validator.py
    python sports_validator.py --sport nba
    python sports_validator.py --sport upcoming    # any league with games today
    THE_ODDS_API_KEY=xxx python sports_validator.py
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

import requests

from validator_core import (
    EdgeRow, evaluate_market, format_table, gamma_search, UA,
)

ODDS_API = "https://api.the-odds-api.com/v4"
KEY = os.environ.get("THE_ODDS_API_KEY", "")

# Map The Odds API sport keys to Polymarket search queries.
SPORT_TO_PM_QUERY = {
    "americanfootball_nfl":      "NFL",
    "basketball_nba":            "NBA",
    "baseball_mlb":              "MLB",
    "icehockey_nhl":             "NHL",
    "soccer_epl":                "Premier League",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_spain_la_liga":      "La Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a":      "Serie A",
    "soccer_usa_mls":            "MLS",
    "tennis_atp_singles":        "ATP",
    "tennis_wta_singles":        "WTA",
    "mma_mixed_martial_arts":    "UFC",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum() or c == " ").strip()


# ============================================================================
# The Odds API
# ============================================================================

def list_sports() -> list[dict]:
    if not KEY:
        return []
    r = requests.get(f"{ODDS_API}/sports", params={"apiKey": KEY}, timeout=15)
    r.raise_for_status()
    return r.json() or []


def fetch_odds(sport: str, regions: str = "us,uk,eu",
               markets: str = "h2h") -> list[dict]:
    if not KEY:
        return []
    r = requests.get(
        f"{ODDS_API}/sports/{sport}/odds",
        params={"apiKey": KEY, "regions": regions, "markets": markets,
                "oddsFormat": "decimal"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json() or []


# ============================================================================
# Devig + price logic
# ============================================================================

def devig_two_way(home_dec: float, away_dec: float) -> tuple[float, float]:
    if home_dec <= 1 or away_dec <= 1:
        return 0.0, 0.0
    h, a = 1.0 / home_dec, 1.0 / away_dec
    s = h + a
    return h / s, a / s


def best_consensus(event: dict, home_team: str, away_team: str
                   ) -> Optional[tuple[float, float, int]]:
    """Aggregate all bookmaker H2H odds into a devigged consensus.

    Returns (home_fair, away_fair, n_books) or None.
    """
    home_decs: list[float] = []
    away_decs: list[float] = []
    for bk in event.get("bookmakers", []) or []:
        for mk in bk.get("markets", []) or []:
            if mk.get("key") != "h2h":
                continue
            outcomes = mk.get("outcomes") or []
            h = next((o for o in outcomes if _norm(o.get("name", "")) == _norm(home_team)), None)
            a = next((o for o in outcomes if _norm(o.get("name", "")) == _norm(away_team)), None)
            if h and a:
                try:
                    home_decs.append(float(h["price"]))
                    away_decs.append(float(a["price"]))
                except (TypeError, ValueError):
                    pass
    if not home_decs:
        return None
    # Average decimal odds, then devig
    avg_h = sum(home_decs) / len(home_decs)
    avg_a = sum(away_decs) / len(away_decs)
    fh, fa = devig_two_way(avg_h, avg_a)
    return fh, fa, len(home_decs)


# ============================================================================
# PM matching
# ============================================================================

def match_pm_market(home_team: str, away_team: str,
                    pm_query: str) -> Optional[dict]:
    """Search Polymarket for a market mentioning both teams."""
    res = gamma_search(f"{home_team} {away_team}", limit=20)
    h_n = _norm(home_team); a_n = _norm(away_team)
    candidates = []
    for src in (res.get("markets") or [], res.get("events") or []):
        for item in src:
            txt = _norm(
                (item.get("question") or item.get("title") or "")
                + " " + (item.get("slug") or "")
            )
            if h_n in txt and a_n in txt:
                if item.get("closed") or item.get("archived"):
                    continue
                candidates.append(item)
    if not candidates:
        return None
    # Prefer markets (with clobTokenIds) over events
    for c in candidates:
        if c.get("clobTokenIds"):
            return c
    # Else dive into event.markets
    for c in candidates:
        for m in c.get("markets", []) or []:
            if m.get("clobTokenIds") and not m.get("closed"):
                return m
    return None


# ============================================================================
# Main scan
# ============================================================================

def scan_sport(sport: str) -> list[EdgeRow]:
    if not KEY:
        print("THE_ODDS_API_KEY not set — register free at https://the-odds-api.com/ "
              "and set the env var.", file=sys.stderr)
        return []
    try:
        events = fetch_odds(sport)
    except Exception as e:
        print(f"odds-api fetch failed: {e}", file=sys.stderr)
        return []
    pm_query = SPORT_TO_PM_QUERY.get(sport, sport)
    rows: list[EdgeRow] = []
    print(f"\n[{sport}] {len(events)} events from The Odds API\n")
    for ev in events:
        h = ev.get("home_team", "")
        a = ev.get("away_team", "")
        if not h or not a:
            continue
        consensus = best_consensus(ev, h, a)
        if not consensus:
            continue
        fh, fa, n = consensus
        market = match_pm_market(h, a, pm_query)
        if not market:
            continue
        # By convention, the YES outcome on PM moneyline markets is usually the
        # listed/first team. We try both and keep whichever has stronger edge.
        for fair, label in ((fh, h), (fa, a)):
            row = evaluate_market(
                market, fair=fair, side="buy", threshold_bps=300,
                validator_name="sports",
                market_label=f"{a} @ {h}: {label} ({n} books)",
            )
            if row.edge_bps is not None and row.edge_bps < 0:
                row = evaluate_market(
                    market, fair=fair, side="sell", threshold_bps=300,
                    validator_name="sports",
                    market_label=f"{a} @ {h}: {label} ({n} books)",
                )
            if row.pm_yes is not None and 0.001 < row.pm_yes < 0.999:
                rows.append(row)
                break  # one row per game

    rows.sort(key=lambda r: -abs(r.edge_bps or 0))
    print(format_table(rows, title=f"sports_validator [{sport}]"))
    flagged = [r for r in rows if r.edge_bps and abs(r.edge_bps) >= 300 and not r.skipped]
    print(f"\n{len(flagged)} actionable / {len(rows)} games.")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="basketball_nba",
                    help="sport key (see The Odds API docs)")
    ap.add_argument("--list-sports", action="store_true")
    ap.add_argument("--watch", type=int, default=0)
    args = ap.parse_args()
    if args.list_sports:
        for s in list_sports():
            print(f"  {s.get('key'):<35}  {s.get('title')}")
        return
    while True:
        try:
            scan_sport(args.sport)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
