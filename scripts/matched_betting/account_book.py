"""
Bookmaker account book — track every UK bookie account, balance, restriction
state, and per-account PnL.

Adjacent to positions.sqlite but separate concerns: positions tracks
specific market positions; bookie_accounts tracks the operator's
infrastructure (which accounts exist, are they restricted, balance trend).

CLI:
    python -m matched_betting.account_book add --bookie Bet365 \
        --opening-balance 30
    python -m matched_betting.account_book list
    python -m matched_betting.account_book bet --account-id 1 \
        --offer-id signup_bet365 --bet-type qualifier --stake 30 \
        --odds 4.5 --selection "Liverpool to win"
    python -m matched_betting.account_book settle --bet-id 5 \
        --outcome won --settlement 105
    python -m matched_betting.account_book pnl
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get(
    "ODDS_BOOKIE_DB", r"C:\Dev\odds\data\bookie_accounts.sqlite"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookie_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bookie TEXT NOT NULL,
    account_label TEXT,
    opening_balance_gbp REAL DEFAULT 0,
    current_balance_gbp REAL DEFAULT 0,
    deposited_total_gbp REAL DEFAULT 0,
    withdrawn_total_gbp REAL DEFAULT 0,
    realised_pnl_gbp REAL DEFAULT 0,
    free_bets_received_gbp REAL DEFAULT 0,
    last_bet_date TEXT,
    restriction_state TEXT DEFAULT 'open',
    notes TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_bookie ON bookie_accounts(bookie);
CREATE INDEX IF NOT EXISTS ix_state ON bookie_accounts(restriction_state);

CREATE TABLE IF NOT EXISTS bookie_bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES bookie_accounts(id),
    offer_id TEXT,
    bet_type TEXT,
    stake_gbp REAL,
    odds REAL,
    selection TEXT,
    market TEXT,
    placed_at TEXT NOT NULL,
    settled_at TEXT,
    outcome TEXT DEFAULT 'pending',
    settlement_gbp REAL,
    lay_position_id INTEGER,
    expected_retention_pct REAL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_bet_account ON bookie_bets(account_id);
CREATE INDEX IF NOT EXISTS ix_bet_outcome ON bookie_bets(outcome);
CREATE INDEX IF NOT EXISTS ix_offer_id ON bookie_bets(offer_id);
"""

VALID_RESTRICTION_STATES = {"open", "soft_restricted", "gubbed", "closed"}
VALID_BET_TYPES = {"qualifier", "free_bet_use", "mug_bet",
                   "reload", "cashout", "casino"}
VALID_OUTCOMES = {"won", "lost", "void", "pending", "half_won", "half_lost"}


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    try:
        yield c
        c.commit()
    finally:
        c.close()


# ============================================================================
# Account ops
# ============================================================================

def add_account(bookie: str, opening_balance: float = 0,
                label: str = "", notes: str = "") -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn() as c:
        cur = c.execute(
            """INSERT INTO bookie_accounts(bookie, account_label,
            opening_balance_gbp, current_balance_gbp, deposited_total_gbp,
            opened_at, notes) VALUES (?,?,?,?,?,?,?)""",
            (bookie, label, opening_balance, opening_balance,
             opening_balance, now, notes),
        )
        return cur.lastrowid


def list_accounts(state: Optional[str] = None) -> list[sqlite3.Row]:
    with conn() as c:
        if state:
            return c.execute(
                "SELECT * FROM bookie_accounts WHERE restriction_state=? "
                "ORDER BY bookie", (state,)).fetchall()
        return c.execute(
            "SELECT * FROM bookie_accounts ORDER BY bookie").fetchall()


def update_balance(account_id: int, new_balance: float,
                   delta_pnl: float = 0) -> None:
    with conn() as c:
        c.execute(
            """UPDATE bookie_accounts SET current_balance_gbp=?,
            realised_pnl_gbp=realised_pnl_gbp+? WHERE id=?""",
            (new_balance, delta_pnl, account_id))


def update_state(account_id: int, state: str, notes: str = "") -> None:
    if state not in VALID_RESTRICTION_STATES:
        raise ValueError(f"state must be one of {VALID_RESTRICTION_STATES}")
    with conn() as c:
        c.execute(
            """UPDATE bookie_accounts SET restriction_state=?,
            notes=COALESCE(notes,'')||?, closed_at=? WHERE id=?""",
            (state, ("\n" + notes) if notes else "",
             dt.datetime.now(dt.timezone.utc).isoformat()
             if state in ("gubbed", "closed") else None, account_id))


# ============================================================================
# Bet ops
# ============================================================================

def record_bet(account_id: int, offer_id: str, bet_type: str,
               stake: float, odds: float, selection: str,
               market: str = "", expected_retention_pct: float = 0,
               notes: str = "") -> int:
    if bet_type not in VALID_BET_TYPES:
        raise ValueError(f"bet_type must be one of {VALID_BET_TYPES}")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn() as c:
        cur = c.execute(
            """INSERT INTO bookie_bets(account_id, offer_id, bet_type, stake_gbp,
            odds, selection, market, placed_at, expected_retention_pct, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (account_id, offer_id, bet_type, stake, odds, selection, market,
             now, expected_retention_pct, notes),
        )
        # Update last_bet_date on account
        c.execute(
            "UPDATE bookie_accounts SET last_bet_date=? WHERE id=?",
            (now, account_id))
        # If qualifier (real money), reduce balance
        if bet_type == "qualifier":
            c.execute(
                """UPDATE bookie_accounts SET current_balance_gbp=
                current_balance_gbp - ? WHERE id=?""",
                (stake, account_id))
        return cur.lastrowid


def settle_bet(bet_id: int, outcome: str, settlement_gbp: float,
               notes: str = "") -> Optional[float]:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn() as c:
        bet = c.execute("SELECT * FROM bookie_bets WHERE id=?",
                        (bet_id,)).fetchone()
        if not bet:
            return None
        c.execute(
            """UPDATE bookie_bets SET outcome=?, settlement_gbp=?, settled_at=?,
            notes=COALESCE(notes,'')||? WHERE id=?""",
            (outcome, settlement_gbp, now,
             ("\n" + notes) if notes else "", bet_id))
        # Adjust account balance + PnL
        # qualifier: stake was already debited; settlement_gbp is GROSS return
        # free_bet_use: stake wasn't debited (it's the bookie's free £);
        #   settlement_gbp is winnings only (or 0 if lose)
        if bet["bet_type"] == "qualifier":
            c.execute(
                """UPDATE bookie_accounts SET current_balance_gbp=
                current_balance_gbp + ?, realised_pnl_gbp=realised_pnl_gbp +
                (? - ?) WHERE id=?""",
                (settlement_gbp, settlement_gbp, bet["stake_gbp"], bet["account_id"]))
        elif bet["bet_type"] == "free_bet_use":
            c.execute(
                """UPDATE bookie_accounts SET current_balance_gbp=
                current_balance_gbp + ?, realised_pnl_gbp=realised_pnl_gbp + ?,
                free_bets_received_gbp=free_bets_received_gbp+?
                WHERE id=?""",
                (settlement_gbp, settlement_gbp, bet["stake_gbp"],
                 bet["account_id"]))
        elif bet["bet_type"] == "mug_bet":
            # stake debited; if won, add back; if lost, no change beyond debit
            if outcome == "won":
                c.execute(
                    """UPDATE bookie_accounts SET current_balance_gbp=
                    current_balance_gbp + ?, realised_pnl_gbp=realised_pnl_gbp+(? - ?)
                    WHERE id=?""",
                    (settlement_gbp, settlement_gbp, bet["stake_gbp"],
                     bet["account_id"]))
            else:
                c.execute(
                    """UPDATE bookie_accounts SET realised_pnl_gbp=
                    realised_pnl_gbp - ? WHERE id=?""",
                    (bet["stake_gbp"], bet["account_id"]))
        return settlement_gbp


def link_lay_position(bet_id: int, position_id: int) -> None:
    """Tie a bookie bet to its Betfair lay (positions.sqlite id)."""
    with conn() as c:
        c.execute("UPDATE bookie_bets SET lay_position_id=? WHERE id=?",
                  (position_id, bet_id))


# ============================================================================
# Reporting
# ============================================================================

def pnl_summary() -> dict:
    with conn() as c:
        rows = c.execute("""
            SELECT bookie,
                   COUNT(*) AS n_accounts,
                   SUM(realised_pnl_gbp) AS total_pnl,
                   SUM(current_balance_gbp) AS total_balance,
                   SUM(free_bets_received_gbp) AS total_free_bets,
                   SUM(CASE WHEN restriction_state='open' THEN 1 ELSE 0 END) AS n_open,
                   SUM(CASE WHEN restriction_state='gubbed' THEN 1 ELSE 0 END) AS n_gubbed
            FROM bookie_accounts GROUP BY bookie""").fetchall()
    return {r["bookie"]: dict(r) for r in rows}


def total_pnl() -> dict:
    with conn() as c:
        row = c.execute("""
            SELECT SUM(realised_pnl_gbp) AS total_pnl,
                   SUM(current_balance_gbp) AS total_balance,
                   SUM(free_bets_received_gbp) AS total_free_bets,
                   COUNT(*) AS n_accounts
            FROM bookie_accounts WHERE restriction_state != 'closed'""").fetchone()
    return dict(row) if row else {}


# ============================================================================
# CLI
# ============================================================================

def _cli():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--bookie", required=True)
    a.add_argument("--opening-balance", type=float, default=0)
    a.add_argument("--label", default="")
    a.add_argument("--notes", default="")

    sub.add_parser("list")

    bal = sub.add_parser("balance")
    bal.add_argument("--account-id", type=int, required=True)
    bal.add_argument("--balance", type=float, required=True)

    st = sub.add_parser("state")
    st.add_argument("--account-id", type=int, required=True)
    st.add_argument("--state", required=True, choices=list(VALID_RESTRICTION_STATES))
    st.add_argument("--notes", default="")

    bet = sub.add_parser("bet")
    bet.add_argument("--account-id", type=int, required=True)
    bet.add_argument("--offer-id", required=True)
    bet.add_argument("--bet-type", required=True, choices=list(VALID_BET_TYPES))
    bet.add_argument("--stake", type=float, required=True)
    bet.add_argument("--odds", type=float, required=True)
    bet.add_argument("--selection", required=True)
    bet.add_argument("--market", default="")

    s = sub.add_parser("settle")
    s.add_argument("--bet-id", type=int, required=True)
    s.add_argument("--outcome", required=True, choices=list(VALID_OUTCOMES))
    s.add_argument("--settlement", type=float, required=True)
    s.add_argument("--notes", default="")

    sub.add_parser("pnl")

    args = ap.parse_args()

    if args.cmd == "add":
        aid = add_account(args.bookie, args.opening_balance, args.label, args.notes)
        print(f"created account id={aid} bookie={args.bookie}")
    elif args.cmd == "list":
        rows = list_accounts()
        if not rows:
            print("(no accounts)"); return
        print(f"{'id':>3}  {'bookie':<14}  {'state':<14}  {'balance':>9}  "
              f"{'pnl':>8}  {'free_bets':>10}  {'last_bet'}")
        for r in rows:
            print(f"{r['id']:>3}  {r['bookie']:<14}  "
                  f"{r['restriction_state']:<14}  "
                  f"£{r['current_balance_gbp']:>8.2f}  "
                  f"£{r['realised_pnl_gbp']:>+7.2f}  "
                  f"£{r['free_bets_received_gbp']:>9.2f}  "
                  f"{(r['last_bet_date'] or '-')[:10]}")
    elif args.cmd == "balance":
        update_balance(args.account_id, args.balance)
        print("ok")
    elif args.cmd == "state":
        update_state(args.account_id, args.state, args.notes)
        print(f"account {args.account_id} -> {args.state}")
    elif args.cmd == "bet":
        bid = record_bet(args.account_id, args.offer_id, args.bet_type,
                         args.stake, args.odds, args.selection, args.market)
        print(f"recorded bet id={bid}")
    elif args.cmd == "settle":
        ret = settle_bet(args.bet_id, args.outcome, args.settlement, args.notes)
        if ret is None:
            print("bet not found", file=sys.stderr); sys.exit(1)
        print(f"settled: £{ret:.2f}")
    elif args.cmd == "pnl":
        s = total_pnl()
        print(f"\nTotal across all open accounts:")
        print(f"  accounts:         {s.get('n_accounts', 0)}")
        print(f"  total balance:    £{s.get('total_balance', 0):.2f}")
        print(f"  total realised:   £{s.get('total_pnl', 0):+.2f}")
        print(f"  free bets used:   £{s.get('total_free_bets', 0):.2f}")
        print(f"\nPer bookie:")
        for bookie, ps in pnl_summary().items():
            print(f"  {bookie:<14}  open:{ps['n_open']}/gubbed:{ps['n_gubbed']}  "
                  f"bal £{ps['total_balance']:.2f}  pnl £{ps['total_pnl']:+.2f}")


if __name__ == "__main__":
    _cli()
