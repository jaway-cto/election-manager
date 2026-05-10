"""
Premier League Golden Boot (Top Goalscorer) model.

Mechanic:
  * For each candidate top-N player, estimate their expected goals over
    remaining fixtures using non-penalty xG/90 + penalty rate × penalty
    taker share + opponent defensive xGA/90.
  * Convert into Poisson-Gamma posterior on remaining-season goals.
  * Monte Carlo: simulate each player's remaining tally + sum to current
    goals, find leader. Estimate P(player wins outright | ties handled by
    Premier League rules: most goals → if tied, fewer games played wins,
    we approximate as 50/50 split among tied leaders).

Inputs (free public data):
  * FBref player stats — we provide a manual seed since the website
    blocks bots; operator pastes in a CSV per gameweek.
  * Understat per-game xG — JSON behind their pages, scrape weekly
  * Fixture list — fixturedownload.com (CSV, free, no auth)
  * Penalty taker designation — manual seed (changes ~3-4× per season)

Output: per-player implied probability + suggested Betfair Top Goalscorer
position (BACK if model_prob > market_prob + threshold, LAY if reversed).

This module recycles the structure of `models/oscar_precursors.py`. The
main difference is Poisson regression (continuous goal-counts) vs logistic
classification (discrete winner).

Usage:
    python -m models.pl_golden_boot                       # report current ranking
    python -m models.pl_golden_boot --simulate 50000      # MC iterations
    python -m models.pl_golden_boot --update-data         # refresh inputs

Data files (operator-maintained):
    data/pl_players.csv      — current player roster + stats
    data/pl_fixtures.csv     — remaining fixtures with home/away
    data/pl_penalty_takers.csv — current penalty taker per club
"""
from __future__ import annotations
import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

DATA_DIR = Path(r"C:\Dev\odds\data")
PLAYERS_CSV = DATA_DIR / "pl_players.csv"
FIXTURES_CSV = DATA_DIR / "pl_fixtures.csv"
PENALTY_CSV = DATA_DIR / "pl_penalty_takers.csv"


@dataclass
class Player:
    name: str
    club: str
    goals_scored: int          # current goals YTD
    minutes_played: int
    npxg_per_90: float         # non-penalty xG per 90 min
    games_played: int
    is_penalty_taker: bool = False


@dataclass
class Fixture:
    home_team: str
    away_team: str
    gameweek: int
    home_def_xga_per_90: float = 1.4   # league-avg fallback
    away_def_xga_per_90: float = 1.4


@dataclass
class TeamStats:
    name: str
    def_xga_per_90: float       # goals conceded per 90 (defensive strength)
    penalty_rate_per_match: float = 0.18  # league avg ~0.2/match


# ============================================================================
# Data loaders
# ============================================================================

def load_players(path: Path = PLAYERS_CSV) -> list[Player]:
    if not path.exists():
        return _seed_players()
    out: list[Player] = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Player(
                name=r["name"], club=r["club"],
                goals_scored=int(r["goals_scored"]),
                minutes_played=int(r["minutes_played"]),
                npxg_per_90=float(r["npxg_per_90"]),
                games_played=int(r["games_played"]),
                is_penalty_taker=r.get("is_penalty_taker", "").lower()
                                 in ("true", "1", "yes"),
            ))
    return out


def load_fixtures(path: Path = FIXTURES_CSV) -> list[Fixture]:
    if not path.exists():
        return []
    out: list[Fixture] = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Fixture(
                home_team=r["home_team"], away_team=r["away_team"],
                gameweek=int(r["gameweek"]),
                home_def_xga_per_90=float(r.get("home_def_xga_per_90", 1.4)),
                away_def_xga_per_90=float(r.get("away_def_xga_per_90", 1.4)),
            ))
    return out


def _seed_players() -> list[Player]:
    """Hand-seeded illustrative dataset. Replace with FBref pull."""
    return [
        Player("Erling Haaland",       "Manchester City", 24, 2400, 1.05, 28, True),
        Player("Mohamed Salah",        "Liverpool",       19, 2700, 0.65, 30, True),
        Player("Cole Palmer",          "Chelsea",         15, 2400, 0.55, 27, True),
        Player("Alexander Isak",       "Newcastle",       14, 2100, 0.65, 24, True),
        Player("Bukayo Saka",          "Arsenal",         10, 2200, 0.40, 25, False),
        Player("Ollie Watkins",        "Aston Villa",     12, 2400, 0.50, 27, False),
        Player("Phil Foden",           "Manchester City",  9, 2200, 0.40, 27, False),
        Player("Heung-min Son",        "Tottenham",       10, 2300, 0.45, 26, False),
    ]


# ============================================================================
# Goal-expectation model
# ============================================================================

def expected_goals_remaining(player: Player,
                             remaining_fixtures: list[Fixture],
                             league_avg_def_xga: float = 1.4) -> float:
    """Sum(per-fixture-expected-goals) for the player.

    Per fixture: (player_npxg_per_90) * (opponent_def_xga / league_avg)
                 * minutes_share_per_match * (1 + penalty_bonus)
    """
    if not remaining_fixtures:
        return 0.0
    minutes_share = min(1.0, (player.minutes_played /
                              max(player.games_played, 1)) / 90.0)
    npxg = 0.0
    for f in remaining_fixtures:
        if player.club == f.home_team:
            opp_def = f.away_def_xga_per_90
        elif player.club == f.away_team:
            opp_def = f.home_def_xga_per_90
        else:
            continue
        opp_factor = opp_def / league_avg_def_xga
        npxg += player.npxg_per_90 * opp_factor * minutes_share
    # Penalty bonus: if player is club's main penalty taker, add ~0.7
    # league-avg penalties × conversion rate × games remaining
    pen_bonus = 0.0
    if player.is_penalty_taker:
        games_remaining = sum(1 for f in remaining_fixtures
                              if player.club in (f.home_team, f.away_team))
        pen_bonus = games_remaining * 0.18 * 0.78  # avg pens × conversion
    return npxg + pen_bonus


# ============================================================================
# Monte Carlo
# ============================================================================

def simulate_winner(players: list[Player],
                    expected_remaining: dict[str, float],
                    n_iter: int = 50_000) -> dict[str, float]:
    if np is None:
        raise RuntimeError("numpy required: pip install numpy")
    final_totals = np.zeros((n_iter, len(players)))
    for i, p in enumerate(players):
        lam = max(expected_remaining.get(p.name, 0), 0.01)
        sampled = np.random.poisson(lam=lam, size=n_iter)
        final_totals[:, i] = p.goals_scored + sampled
    # Winner per iteration: max with random tiebreak
    rand_break = np.random.random(final_totals.shape) * 1e-6
    rankable = final_totals + rand_break
    winners = np.argmax(rankable, axis=1)
    counts = {p.name: 0 for p in players}
    for w in winners:
        counts[players[w].name] += 1
    return {n: c / n_iter for n, c in counts.items()}


# ============================================================================
# Reporting / market comparison
# ============================================================================

def report(players: list[Player], fixtures: list[Fixture],
           market_prices: Optional[dict[str, float]] = None,
           n_iter: int = 50_000) -> list[dict]:
    expected = {p.name: expected_goals_remaining(p, fixtures) for p in players}
    probs = simulate_winner(players, expected, n_iter)
    rows = []
    for p in players:
        prob = probs[p.name]
        market = (market_prices or {}).get(p.name)
        edge = (prob - market) * 100 if market is not None else None
        rows.append({
            "name": p.name, "club": p.club,
            "goals": p.goals_scored,
            "expected_remaining": round(expected[p.name], 2),
            "model_prob": round(prob, 3),
            "market_prob": market,
            "edge_pp": round(edge, 1) if edge is not None else None,
        })
    rows.sort(key=lambda r: -r["model_prob"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", type=int, default=50_000)
    ap.add_argument("--players", type=Path, default=PLAYERS_CSV)
    ap.add_argument("--fixtures", type=Path, default=FIXTURES_CSV)
    args = ap.parse_args()
    players = load_players(args.players)
    fixtures = load_fixtures(args.fixtures)
    if not fixtures:
        print(f"WARN: no fixtures loaded from {args.fixtures}; results "
              f"will assume zero remaining games.", file=sys.stderr)
    rows = report(players, fixtures, n_iter=args.simulate)
    print(f"\nPL Golden Boot model — {args.simulate:,} MC iterations\n")
    print(f"{'Player':<22}{'Club':<22}{'Goals':>6}{'xRem':>6}"
          f"{'Model%':>8}{'Market%':>9}{'Edge':>8}")
    print("-" * 81)
    for r in rows:
        m = f"{r['market_prob']*100:.1f}%" if r['market_prob'] else "  -  "
        e = f"{r['edge_pp']:+5.1f}pp" if r['edge_pp'] is not None else "   -  "
        print(f"{r['name']:<22}{r['club']:<22}{r['goals']:>6}"
              f"{r['expected_remaining']:>6.1f}"
              f"{r['model_prob']*100:>7.1f}%{m:>9}{e:>8}")


if __name__ == "__main__":
    main()
