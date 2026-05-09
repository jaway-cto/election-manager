"""
NBA moneyline arbitrage scanner: Polymarket vs Bovada (devigged) + ESPN cross-check.

Usage:
    python nba_validator.py
    python nba_validator.py --watch        # refresh every 60s
    python nba_validator.py --watch 30     # refresh every 30s
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Iterable

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

BOVADA_NBA = (
    "https://www.bovada.lv/services/sports/event/coupon/events/A"
    "/description/basketball/nba"
)
ESPN_NBA = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
PM_GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
PM_GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"

EDGE_FLAG = 0.03  # 3 percentage points


# ---------- Math ----------
def devig_two_way(home_dec: float, away_dec: float) -> tuple[float, float]:
    p_h, p_a = 1.0 / home_dec, 1.0 / away_dec
    t = p_h + p_a
    return p_h / t, p_a / t


# ---------- Data classes ----------
@dataclass
class Game:
    home: str
    away: str
    bov_home_dec: float | None = None
    bov_away_dec: float | None = None
    pm_home_yes: float | None = None  # implied prob from Polymarket
    pm_slug: str | None = None
    espn_status: str | None = None


# ---------- Sources ----------
def fetch_bovada() -> list[Game]:
    try:
        r = requests.get(BOVADA_NBA, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[bovada] error: {e}", file=sys.stderr)
        return []

    games: list[Game] = []
    for block in data:
        for ev in block.get("events", []):
            comps = ev.get("competitors") or []
            home = next((c["name"] for c in comps if c.get("home")), None)
            away = next((c["name"] for c in comps if not c.get("home")), None)
            if not (home and away):
                continue
            ml_home = ml_away = None
            for dg in ev.get("displayGroups", []):
                for m in dg.get("markets", []):
                    if m.get("description") != "Moneyline":
                        continue
                    if m.get("period", {}).get("description") not in (
                        "Match",
                        "Game",
                        "Regular Time",
                    ):
                        # accept anyway if only one moneyline present
                        pass
                    for o in m.get("outcomes", []):
                        dec = o.get("price", {}).get("decimal")
                        if not dec:
                            continue
                        try:
                            dec = float(dec)
                        except ValueError:
                            continue
                        desc = o.get("description", "")
                        if desc == home:
                            ml_home = dec
                        elif desc == away:
                            ml_away = dec
                    if ml_home and ml_away:
                        break
                if ml_home and ml_away:
                    break
            games.append(
                Game(
                    home=home,
                    away=away,
                    bov_home_dec=ml_home,
                    bov_away_dec=ml_away,
                )
            )
    return games


def fetch_espn() -> dict[str, str]:
    try:
        r = requests.get(ESPN_NBA, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[espn] error: {e}", file=sys.stderr)
        return {}
    out: dict[str, str] = {}
    for ev in data.get("events", []):
        name = ev.get("name", "")  # "Away at Home"
        status = ev.get("status", {}).get("type", {}).get("shortDetail", "")
        out[name.lower()] = status
    return out


def fetch_polymarket_nba() -> list[dict]:
    """Return active NBA-related markets from Gamma."""
    out: list[dict] = []
    # Try events endpoint with NBA tag/keyword
    for params in (
        {"closed": "false", "limit": 200, "tag_slug": "nba"},
        {"closed": "false", "limit": 200, "tag": "NBA"},
        {"active": "true", "closed": "false", "limit": 500},
    ):
        try:
            r = requests.get(PM_GAMMA_EVENTS, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[pm events {params}] {e}", file=sys.stderr)
            continue
        if isinstance(data, list) and data:
            out.extend(data)
            break
    # Also pull markets directly
    try:
        r = requests.get(
            PM_GAMMA_MARKETS,
            params={"closed": "false", "active": "true", "limit": 500},
            timeout=15,
        )
        r.raise_for_status()
        out.extend(r.json() or [])
    except Exception as e:
        print(f"[pm markets] {e}", file=sys.stderr)
    return out


# ---------- Matching ----------
TEAM_NICKS = {
    "philadelphia 76ers": "76ers",
    "new york knicks": "knicks",
    "san antonio spurs": "spurs",
    "minnesota timberwolves": "timberwolves",
    "oklahoma city thunder": "thunder",
    "denver nuggets": "nuggets",
    "indiana pacers": "pacers",
    "cleveland cavaliers": "cavaliers",
    "boston celtics": "celtics",
    "los angeles lakers": "lakers",
    "la clippers": "clippers",
    "los angeles clippers": "clippers",
    "golden state warriors": "warriors",
    "milwaukee bucks": "bucks",
    "miami heat": "heat",
    "orlando magic": "magic",
    "houston rockets": "rockets",
    "memphis grizzlies": "grizzlies",
    "dallas mavericks": "mavericks",
    "phoenix suns": "suns",
    "sacramento kings": "kings",
    "portland trail blazers": "trail blazers",
    "atlanta hawks": "hawks",
    "charlotte hornets": "hornets",
    "washington wizards": "wizards",
    "toronto raptors": "raptors",
    "brooklyn nets": "nets",
    "chicago bulls": "bulls",
    "detroit pistons": "pistons",
    "utah jazz": "jazz",
    "new orleans pelicans": "pelicans",
}


def nick(team: str) -> str:
    t = team.lower().strip()
    return TEAM_NICKS.get(t, t.split()[-1] if t else t)


def match_pm(game: Game, pm_items: Iterable[dict]) -> tuple[float, str, dict] | None:
    h, a = nick(game.home), nick(game.away)
    # Prefer non-series, non-closed markets whose slug looks like a single game
    def score(it: dict) -> int:
        slug = (it.get("slug") or "").lower()
        s = 0
        if "series" in slug or "playoffs-who" in slug or "champion" in slug:
            s -= 100
        if it.get("closed") or it.get("archived"):
            s -= 200
        # accept resolved=False
        if it.get("active") is False:
            s -= 50
        # game-style slug bonus
        if any(tok in slug for tok in ("nba-", "-2026-")):
            s += 10
        return s

    candidates = []
    for it in pm_items:
        title = (
            (it.get("title") or it.get("question") or it.get("slug") or "")
            .lower()
        )
        if not title:
            continue
        if h in title and a in title:
            candidates.append(it)
    candidates.sort(key=score, reverse=True)
    for it in candidates:
        if it.get("closed") or it.get("archived"):
            continue
        slug_l = (it.get("slug") or "").lower()
        if "series" in slug_l or "champion" in slug_l or "who-will-win" in slug_l:
            continue
        # Try to pull YES price for home team
        outcomes = it.get("outcomes")
        prices = it.get("outcomePrices")
        if isinstance(outcomes, str):
            import json as _j
            try:
                outcomes = _j.loads(outcomes)
                prices = _j.loads(prices) if isinstance(prices, str) else prices
            except Exception:
                outcomes = None
        slug = it.get("slug", "")
        if outcomes and prices and len(outcomes) == len(prices):
            for o, p in zip(outcomes, prices):
                if h in str(o).lower():
                    try:
                        return float(p), slug, it
                    except (TypeError, ValueError):
                        pass
            try:
                return float(prices[0]), slug, it
            except (TypeError, ValueError):
                pass
        # nested markets list (events) — pick the plain game ML market
        for sub in it.get("markets", []) or []:
            sub_slug = (sub.get("slug") or "").lower()
            if any(tok in sub_slug for tok in ("spread", "total", "1h", "1q", "h2h-")):
                continue
            if sub.get("closed") or sub.get("archived"):
                continue
            res = match_pm_market(sub, h)
            if res:
                return res[0], slug or sub.get("slug", ""), sub
    return None


def match_pm_market(m: dict, home_nick: str) -> tuple[float, str] | None:
    import json as _j
    outcomes = m.get("outcomes")
    prices = m.get("outcomePrices")
    if isinstance(outcomes, str):
        try:
            outcomes = _j.loads(outcomes)
            prices = _j.loads(prices) if isinstance(prices, str) else prices
        except Exception:
            return None
    if not (outcomes and prices):
        return None
    for o, p in zip(outcomes, prices):
        if home_nick in str(o).lower():
            try:
                return float(p), m.get("slug", "")
            except Exception:
                return None
    return None


# CLOB enrichment for NBA markets
def _nba_clob_quote(market: dict, home_nick: str):
    """Fetch CLOB quote for the home team's YES token. Returns Quote or None."""
    try:
        from validator_core import get_quote, parse_clob_token_ids
    except ImportError:
        return None
    yes_tok, no_tok = parse_clob_token_ids(market)
    # The yes token might correspond to home or away depending on outcomes order.
    # Use outcomes to figure out which token is the home team.
    import json as _j
    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        try:
            outcomes = _j.loads(outcomes)
        except Exception:
            outcomes = None
    if outcomes and len(outcomes) >= 2:
        if home_nick in str(outcomes[0]).lower():
            return get_quote(yes_tok) if yes_tok else None
        if home_nick in str(outcomes[1]).lower():
            return get_quote(no_tok) if no_tok else None
    return get_quote(yes_tok) if yes_tok else None


# ---------- Scan ----------
def scan_nba() -> None:
    bov = fetch_bovada()
    espn = fetch_espn()
    pm = fetch_polymarket_nba()

    print(f"\nBovada games: {len(bov)} | ESPN events: {len(espn)} | "
          f"PM items: {len(pm)}\n")

    header = (
        f"{'Matchup':<38}{'Bov fair H%':>12}{'PM YES H%':>12}"
        f"{'Edge':>10}  Status / signal"
    )
    print(header)
    print("-" * len(header))

    for g in bov:
        if not (g.bov_home_dec and g.bov_away_dec):
            continue
        fair_h, fair_a = devig_two_way(g.bov_home_dec, g.bov_away_dec)
        match = match_pm(g, pm)
        pm_h = match[0] if match else None
        slug = match[1] if match else ""
        market = match[2] if match and len(match) > 2 else None
        # CLOB upgrade: if we have the market dict, fetch executable price + spread.
        spread_bps = None
        if market is not None:
            quote = _nba_clob_quote(market, nick(g.home))
            if quote and quote.has_book:
                # Use ask if buying home (edge positive), bid if selling.
                # Edge sign computed below; for now grab spread + use mid as "PM price".
                spread_bps = quote.spread_bps
                if quote.mid is not None:
                    pm_h = quote.mid
        key = f"{g.away} at {g.home}".lower()
        status = espn.get(key, "")
        edge = (fair_h - pm_h) if pm_h is not None else None

        matchup = f"{g.away} @ {g.home}"
        bov_str = f"{fair_h*100:>10.1f}%"
        sp_str = f"{spread_bps:.0f}bp" if spread_bps is not None else "  -  "
        if pm_h is None:
            pm_str = f"{'-':>11}"
            edge_str = f"{'-':>9}"
            signal = "no PM market matched"
        else:
            pm_str = f"{pm_h*100:>10.1f}%"
            edge_str = f"{edge*100:>+8.1f}pp"
            if abs(edge) > EDGE_FLAG:
                if edge > 0:
                    base = f"BUY {nick(g.home)} YES on PM (Bov fair > PM)"
                else:
                    base = f"BUY {nick(g.away)} (PM home overpriced)"
                edge_bps = abs(edge) * 10000
                if spread_bps and spread_bps > edge_bps * 0.5:
                    signal = base + f" -- SPREAD EATS EDGE"
                else:
                    signal = "** " + base
            else:
                signal = "no edge"
            if slug:
                signal += f" [{slug[:40]}]"
        if status:
            signal = f"{status} | {signal}"
        print(f"{matchup:<38}{bov_str:>12}{pm_str:>12}{edge_str:>10}{sp_str:>7}  {signal}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", nargs="?", const=60, type=int, default=None,
                    help="refresh interval in seconds")
    args = ap.parse_args()
    if args.watch:
        while True:
            print("\n" + "=" * 80)
            print(time.strftime("%Y-%m-%d %H:%M:%S"))
            try:
                scan_nba()
            except Exception as e:
                print(f"scan error: {e}", file=sys.stderr)
            time.sleep(args.watch)
    else:
        scan_nba()


if __name__ == "__main__":
    main()
