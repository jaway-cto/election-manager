"""
Matched-betting lay calculator — pure logic.

The fundamental matched-betting math: for any free-bet promotion at a
bookie, compute the optimal lay stake at Betfair (or Smarkets) so that
PnL is approximately equal regardless of outcome. Locks in a fixed % of
the free-bet face value as risk-free profit.

Two free-bet types covered:
  * SNR (Stake Not Returned) — most UK signup offers. The free-bet stake
    is consumed regardless of outcome; you only receive winnings.
  * SR  (Stake Returned) — rarer. The bookie returns the stake plus
    winnings on a winning bet.

Plus qualifying-loss math for the bet you place to UNLOCK the free bet.

Output for every offer:
  * Optimal lay stake (£)
  * Lay liability required (£)
  * Net retention (% of free-bet face value)
  * Win/lose outcome PnLs (should match within rounding)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


BETFAIR_COMMISSION_DEFAULT = 0.05    # 5% Betfair retail
SMARKETS_COMMISSION_DEFAULT = 0.02   # 2% Smarkets standard


@dataclass
class LayResult:
    lay_stake: float
    liability: float
    win_pnl: float
    lose_pnl: float
    net_retention_pct: float   # (avg_pnl / free_bet_amount) * 100
    free_bet_type: str
    bookie_odds: float
    lay_odds: float
    commission: float
    notes: str = ""


def lay_stake_qualifier(back_stake: float, back_odds: float,
                        lay_odds: float,
                        commission: float = BETFAIR_COMMISSION_DEFAULT
                        ) -> LayResult:
    """For a QUALIFYING bet (your own £ at risk to unlock the free bet).

    Goal: equal PnL on win/lose so qualifying loss is minimised.
    Formula: lay_stake = (back_stake * back_odds) / (lay_odds - commission)
    """
    if back_odds <= 1 or lay_odds <= 1:
        raise ValueError("odds must be > 1")
    lay_stake = (back_stake * back_odds) / (lay_odds - commission)
    liability = lay_stake * (lay_odds - 1)
    # Outcomes:
    win_book = back_stake * (back_odds - 1)
    win_pnl = win_book - liability
    lose_pnl = -back_stake + lay_stake * (1 - commission)
    avg = (win_pnl + lose_pnl) / 2.0
    return LayResult(
        lay_stake=round(lay_stake, 2), liability=round(liability, 2),
        win_pnl=round(win_pnl, 2), lose_pnl=round(lose_pnl, 2),
        net_retention_pct=round((avg / back_stake) * 100, 2),
        free_bet_type="qualifier", bookie_odds=back_odds, lay_odds=lay_odds,
        commission=commission,
        notes="Negative net retention is expected qualifying loss; "
              "free bet to follow has positive EV that more than covers it.",
    )


def lay_stake_free_bet_snr(free_bet_amount: float, back_odds: float,
                           lay_odds: float,
                           commission: float = BETFAIR_COMMISSION_DEFAULT
                           ) -> LayResult:
    """For a STAKE-NOT-RETURNED free bet (most common UK signup offer).

    The free-bet stake is consumed; you only receive winnings if it wins.
    Lay formula: lay_stake = free_bet_amount * (back_odds - 1) / (lay_odds - commission)
    """
    if back_odds <= 1 or lay_odds <= 1:
        raise ValueError("odds must be > 1")
    lay_stake = (free_bet_amount * (back_odds - 1)) / (lay_odds - commission)
    liability = lay_stake * (lay_odds - 1)
    # Win at bookie: receive free_bet_amount * (back_odds - 1) (winnings only;
    # stake not returned). Lose lay: -liability.
    win_book = free_bet_amount * (back_odds - 1)
    win_pnl = win_book - liability
    # Lose bookie: 0 (free bet consumed). Win lay: lay_stake * (1 - commission).
    lose_pnl = lay_stake * (1 - commission)
    avg = (win_pnl + lose_pnl) / 2.0
    return LayResult(
        lay_stake=round(lay_stake, 2), liability=round(liability, 2),
        win_pnl=round(win_pnl, 2), lose_pnl=round(lose_pnl, 2),
        net_retention_pct=round((avg / free_bet_amount) * 100, 2),
        free_bet_type="SNR", bookie_odds=back_odds, lay_odds=lay_odds,
        commission=commission,
        notes="Optimal back odds for SNR are typically 5.0-8.0 "
              "(maximises retention).",
    )


def lay_stake_free_bet_sr(free_bet_amount: float, back_odds: float,
                          lay_odds: float,
                          commission: float = BETFAIR_COMMISSION_DEFAULT
                          ) -> LayResult:
    """For a STAKE-RETURNED free bet (rare). Stake returns on win.
    Lay formula: lay_stake = free_bet_amount * back_odds / (lay_odds - commission)
    """
    if back_odds <= 1 or lay_odds <= 1:
        raise ValueError("odds must be > 1")
    lay_stake = (free_bet_amount * back_odds) / (lay_odds - commission)
    liability = lay_stake * (lay_odds - 1)
    win_book = free_bet_amount * (back_odds - 1) + free_bet_amount  # incl. stake
    win_pnl = win_book - liability
    lose_pnl = lay_stake * (1 - commission)
    avg = (win_pnl + lose_pnl) / 2.0
    return LayResult(
        lay_stake=round(lay_stake, 2), liability=round(liability, 2),
        win_pnl=round(win_pnl, 2), lose_pnl=round(lose_pnl, 2),
        net_retention_pct=round((avg / free_bet_amount) * 100, 2),
        free_bet_type="SR", bookie_odds=back_odds, lay_odds=lay_odds,
        commission=commission,
    )


def find_optimal_lay_market(back_odds: float, betfair_quotes: list[dict],
                            offer_type: str = "SNR",
                            free_bet_amount: float = 25.0,
                            commission: float = BETFAIR_COMMISSION_DEFAULT
                            ) -> Optional[dict]:
    """Given a list of Betfair markets that match the bookie's bet, return
    the one whose lay odds maximise net retention.

    betfair_quotes: list of {market_id, runner_name, lay_odds}
    Returns: the chosen market dict + LayResult, or None if no positive
    retention market found.
    """
    if not betfair_quotes:
        return None
    best = None
    best_ret = -float("inf")
    for q in betfair_quotes:
        lay_odds = q.get("lay_odds", 0)
        if lay_odds <= 1:
            continue
        if offer_type.upper() == "SNR":
            r = lay_stake_free_bet_snr(free_bet_amount, back_odds,
                                       lay_odds, commission)
        else:
            r = lay_stake_free_bet_sr(free_bet_amount, back_odds,
                                      lay_odds, commission)
        if r.net_retention_pct > best_ret:
            best = {"market": q, "calc": r}
            best_ret = r.net_retention_pct
    return best


# ============================================================================
# CLI for quick interactive use
# ============================================================================

def _cli():
    import argparse
    ap = argparse.ArgumentParser(
        description="Matched-betting lay calculator. Pick mode + supply odds.")
    ap.add_argument("mode", choices=["qual", "snr", "sr"],
                    help="qual = qualifier loss, snr = stake-not-returned "
                         "free bet, sr = stake-returned free bet")
    ap.add_argument("--back-stake", type=float, required=True,
                    help="for qual: your stake; for snr/sr: free-bet face value")
    ap.add_argument("--back-odds", type=float, required=True)
    ap.add_argument("--lay-odds", type=float, required=True)
    ap.add_argument("--commission", type=float,
                    default=BETFAIR_COMMISSION_DEFAULT,
                    help=f"default {BETFAIR_COMMISSION_DEFAULT}")
    args = ap.parse_args()
    if args.mode == "qual":
        r = lay_stake_qualifier(args.back_stake, args.back_odds, args.lay_odds,
                                args.commission)
    elif args.mode == "snr":
        r = lay_stake_free_bet_snr(args.back_stake, args.back_odds,
                                   args.lay_odds, args.commission)
    else:
        r = lay_stake_free_bet_sr(args.back_stake, args.back_odds,
                                  args.lay_odds, args.commission)
    print(f"\n{r.free_bet_type} matched bet:")
    print(f"  Bookie odds:       {r.bookie_odds}")
    print(f"  Lay (Betfair):     {r.lay_odds}  (commission {r.commission*100:.0f}%)")
    print(f"  Lay stake:         £{r.lay_stake:.2f}")
    print(f"  Lay liability:     £{r.liability:.2f}")
    print(f"  PnL if bookie wins: £{r.win_pnl:+.2f}")
    print(f"  PnL if bookie loses: £{r.lose_pnl:+.2f}")
    print(f"  Net retention:     {r.net_retention_pct:.1f}% of free-bet face")
    if r.notes:
        print(f"  Note: {r.notes}")


if __name__ == "__main__":
    _cli()
