"""
positions.py — SQLite position book.

Single source of truth for what we hold, what edge we entered at, and
mark-to-market PnL vs current CLOB mids.

Schema:
    positions(
        id INTEGER PRIMARY KEY,
        market_id TEXT,           -- Polymarket gamma market id
        token_id TEXT,            -- CLOB outcome token id
        venue TEXT,               -- 'polymarket' / 'betfair' / 'kalshi' / etc.
        side TEXT,                -- 'BUY' or 'SELL'
        size REAL,                -- shares (or notional in $ for non-CLOB)
        entry_px REAL,            -- entry price (0-1 for binary contracts)
        fair_at_entry REAL,       -- our model's fair value at entry
        edge_bps_at_entry REAL,
        validator TEXT,           -- which scanner generated this signal
        market_label TEXT,        -- human-readable
        opened_at TEXT,           -- ISO UTC
        closed_at TEXT,
        exit_px REAL,
        pnl_realised REAL,
        notes TEXT,
        status TEXT               -- 'open' | 'closed' | 'cancelled'
    )

    fills(
        id INTEGER PRIMARY KEY,
        position_id INTEGER,
        ts TEXT,
        side TEXT,
        size REAL,
        px REAL,
        fee REAL,
        order_id TEXT
    )

CLI:
    python positions.py list                    # all open
    python positions.py list --closed
    python positions.py mtm                     # mark-to-market all open
    python positions.py open <market_id> <token_id> <side> <size> <px>
    python positions.py close <position_id> <exit_px>
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

DB_PATH = Path(os.environ.get("ODDS_DB",
                              r"C:\Dev\odds\data\positions.sqlite"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    token_id TEXT,
    venue TEXT NOT NULL DEFAULT 'polymarket',
    side TEXT NOT NULL,
    size REAL NOT NULL,
    entry_px REAL NOT NULL,
    fair_at_entry REAL,
    edge_bps_at_entry REAL,
    validator TEXT,
    market_label TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_px REAL,
    pnl_realised REAL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS ix_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS ix_positions_market ON positions(market_id);
CREATE INDEX IF NOT EXISTS ix_positions_validator ON positions(validator);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL REFERENCES positions(id),
    ts TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    px REAL NOT NULL,
    fee REAL DEFAULT 0,
    order_id TEXT
);

CREATE TABLE IF NOT EXISTS heartbeats (
    ts TEXT PRIMARY KEY,
    source TEXT,
    payload TEXT
);
"""


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


def open_position(market_id: str, token_id: str, side: str,
                  size: float, entry_px: float, *,
                  fair_at_entry: Optional[float] = None,
                  edge_bps_at_entry: Optional[float] = None,
                  validator: str = "manual",
                  market_label: str = "",
                  venue: str = "polymarket",
                  notes: str = "") -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn() as c:
        cur = c.execute(
            """INSERT INTO positions
            (market_id, token_id, venue, side, size, entry_px, fair_at_entry,
             edge_bps_at_entry, validator, market_label, opened_at, notes, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'open')""",
            (market_id, token_id, venue, side, size, entry_px,
             fair_at_entry, edge_bps_at_entry, validator,
             market_label, now, notes),
        )
        pid = cur.lastrowid
        c.execute(
            """INSERT INTO fills (position_id, ts, side, size, px)
            VALUES (?,?,?,?,?)""",
            (pid, now, side, size, entry_px),
        )
    return pid


def close_position(position_id: int, exit_px: float,
                   notes: str = "") -> Optional[float]:
    """Returns realised pnl in $ (per share unit), or None if not found."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn() as c:
        row = c.execute("SELECT * FROM positions WHERE id=?",
                        (position_id,)).fetchone()
        if not row or row["status"] != "open":
            return None
        size = float(row["size"]); entry = float(row["entry_px"])
        side = row["side"]
        # PnL: BUY profits when exit > entry; SELL profits when entry > exit.
        if side == "BUY":
            pnl = (exit_px - entry) * size
        else:
            pnl = (entry - exit_px) * size
        c.execute(
            """UPDATE positions SET status='closed', exit_px=?, closed_at=?,
               pnl_realised=?, notes=COALESCE(notes,'')||?
               WHERE id=?""",
            (exit_px, now, pnl, ("\n" + notes) if notes else "", position_id),
        )
        c.execute(
            """INSERT INTO fills (position_id, ts, side, size, px)
            VALUES (?,?,?,?,?)""",
            (position_id, now,
             "SELL" if side == "BUY" else "BUY",
             size, exit_px),
        )
        return pnl


def list_open() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT * FROM positions WHERE status='open' ORDER BY opened_at"
        ).fetchall()


def list_all(limit: int = 200) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT * FROM positions ORDER BY opened_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def pnl_by_validator() -> dict:
    with conn() as c:
        rows = c.execute("""
            SELECT validator,
                   SUM(pnl_realised) AS realised,
                   COUNT(*) AS n,
                   SUM(CASE WHEN pnl_realised > 0 THEN 1 ELSE 0 END) AS wins
            FROM positions WHERE status='closed' AND pnl_realised IS NOT NULL
            GROUP BY validator
        """).fetchall()
    return {r["validator"]: {"realised": r["realised"] or 0,
                             "n": r["n"], "wins": r["wins"]}
            for r in rows}


def mark_to_market(price_lookup) -> list[dict]:
    """Mark all open positions at current prices.

    price_lookup: callable token_id -> current_price (or None if unknown)
    Returns list of {position_id, mtm_pnl, current_px}.
    """
    out: list[dict] = []
    for row in list_open():
        px = price_lookup(row["token_id"])
        if px is None:
            continue
        size = float(row["size"]); entry = float(row["entry_px"])
        if row["side"] == "BUY":
            pnl = (px - entry) * size
        else:
            pnl = (entry - px) * size
        out.append({
            "position_id": row["id"],
            "market_label": row["market_label"],
            "side": row["side"], "size": size,
            "entry_px": entry, "current_px": px,
            "mtm_pnl": pnl, "validator": row["validator"],
        })
    return out


def heartbeat(source: str, payload: str = "") -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO heartbeats(ts, source, payload) VALUES (?,?,?)",
            (now, source, payload),
        )


# ----------------------- CLI -----------------------

def _print_rows(rows):
    if not rows:
        print("(none)"); return
    cols = ("id", "validator", "side", "size", "entry_px", "exit_px",
            "pnl_realised", "status", "market_label")
    print("  ".join(f"{c:<14}" for c in cols))
    for r in rows:
        vals = []
        for c in cols:
            v = r[c]
            if v is None:
                v = "-"
            elif isinstance(v, float):
                v = f"{v:.4f}" if c.endswith("px") else f"{v:.2f}"
            vals.append(str(v)[:14])
        print("  ".join(f"{v:<14}" for v in vals))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("all")
    sub.add_parser("mtm")
    sub.add_parser("pnl")
    op = sub.add_parser("open")
    op.add_argument("--market-id", required=True)
    op.add_argument("--token-id", required=True)
    op.add_argument("--side", choices=["BUY", "SELL"], required=True)
    op.add_argument("--size", type=float, required=True)
    op.add_argument("--px", type=float, required=True)
    op.add_argument("--fair", type=float)
    op.add_argument("--validator", default="manual")
    op.add_argument("--label", default="")
    cl = sub.add_parser("close")
    cl.add_argument("--id", type=int, required=True)
    cl.add_argument("--px", type=float, required=True)
    cl.add_argument("--notes", default="")

    args = ap.parse_args()
    if args.cmd == "list":
        _print_rows(list_open())
    elif args.cmd == "all":
        _print_rows(list_all())
    elif args.cmd == "open":
        pid = open_position(
            args.market_id, args.token_id, args.side, args.size, args.px,
            fair_at_entry=args.fair, validator=args.validator,
            market_label=args.label,
        )
        print(f"opened position id={pid}")
    elif args.cmd == "close":
        pnl = close_position(args.id, args.px, args.notes)
        if pnl is None:
            print("not found or already closed", file=sys.stderr); sys.exit(1)
        print(f"closed id={args.id}  pnl={pnl:+.4f}")
    elif args.cmd == "pnl":
        for v, s in pnl_by_validator().items():
            print(f"  {v:<16}  n={s['n']:>3}  wins={s['wins']:>3}  pnl=${s['realised']:+.2f}")
    elif args.cmd == "mtm":
        from validator_core import fetch_clob_midpoint
        rows = mark_to_market(fetch_clob_midpoint)
        if not rows:
            print("(no open positions or no prices)"); return
        total = sum(r["mtm_pnl"] for r in rows)
        for r in rows:
            print(f"  #{r['position_id']:<4}  {r['side']:<4} {r['size']:>6.1f} "
                  f"@ {r['entry_px']:.4f} -> {r['current_px']:.4f}  "
                  f"pnl ${r['mtm_pnl']:+.2f}   {r['market_label'][:40]}")
        print(f"\nTotal MTM: ${total:+.2f}")


if __name__ == "__main__":
    main()
