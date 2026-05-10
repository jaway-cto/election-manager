r"""
trade_gateway.py — Unified, safety-gated trade execution layer.

ALL order placement (Betfair, Smarkets, Polymarket where allowed) flows
through this module. Direct calls to venue clients should be reserved for
read-only scanning. Trading goes through the gateway so:

  * Killswitch is always checked
  * Per-trade £/$ caps are enforced
  * Daily cumulative cap is enforced
  * Trades > APPROVAL_THRESHOLD send a Telegram message and require an
    /approve <signal_id> reply within timeout
  * Every attempt (including denied) is audit-logged
  * Position book is updated on success
  * Killswitch trip during a trade aborts the placement immediately

Used by both:
  * CLI scanners (auto-execute paths)
  * MCP server (when AI calls place_order tool)

Configuration via env:
    GATEWAY_PER_TRADE_CAP       default £100
    GATEWAY_DAILY_CAP           default £300
    GATEWAY_APPROVAL_THRESHOLD  default £25 (orders ≥ require Telegram /approve)
    GATEWAY_VENUE_BETFAIR       '1' to enable Betfair execution (default off)
    GATEWAY_VENUE_SMARKETS      '1' to enable Smarkets execution (default off)
    GATEWAY_AUDIT_LOG           default C:\Dev\odds\data\trade_audit.jsonl
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Optional

import killswitch
from notify import alert, fyi, event as log_event

# ============================================================================
# Configuration
# ============================================================================

PER_TRADE_CAP_GBP = float(os.environ.get("GATEWAY_PER_TRADE_CAP", "100"))
DAILY_CAP_GBP = float(os.environ.get("GATEWAY_DAILY_CAP", "300"))
APPROVAL_THRESHOLD_GBP = float(os.environ.get("GATEWAY_APPROVAL_THRESHOLD", "25"))
VENUE_BETFAIR_ENABLED = os.environ.get("GATEWAY_VENUE_BETFAIR") == "1"
VENUE_SMARKETS_ENABLED = os.environ.get("GATEWAY_VENUE_SMARKETS") == "1"
AUDIT_LOG_PATH = Path(os.environ.get(
    "GATEWAY_AUDIT_LOG", r"C:\Dev\odds\data\trade_audit.jsonl"))
AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

APPROVAL_TIMEOUT_SECONDS = 300  # 5 min default — user can /approve <id>


# ============================================================================
# Types
# ============================================================================

class Venue(str, Enum):
    BETFAIR = "betfair"
    SMARKETS = "smarkets"
    POLYMARKET = "polymarket"  # blocked from UK; gateway will refuse


class Side(str, Enum):
    BACK = "back"      # = BUY YES on Polymarket conventions
    LAY = "lay"        # = SELL YES on Polymarket conventions
    BUY = "buy"        # Smarkets terminology
    SELL = "sell"


@dataclass
class OrderRequest:
    venue: Venue
    market_id: str          # venue-native id
    selection_id: str       # contract / runner id within market
    side: Side
    price: float            # decimal odds (Betfair) or 0..1 prob (PM/Smarkets)
    stake_gbp: float        # GBP equivalent
    market_label: str = ""  # human-readable for alerts/audit
    rationale: str = ""     # why this trade — comes from scanner or AI
    signal_id: Optional[str] = None  # if from a scanner
    requires_approval: bool = False
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class OrderResult:
    request: OrderRequest
    status: str             # 'placed' | 'rejected' | 'awaiting_approval' | 'failed' | 'denied_by_gate'
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    fill_size: Optional[float] = None
    reason: str = ""
    placed_at: Optional[str] = None


# ============================================================================
# Audit log + daily cap tracking
# ============================================================================

def _audit(rec: dict) -> None:
    rec = dict(rec)
    rec["ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        sys.stderr.write(f"[gateway] audit write failed: {e}\n")


def _stake_today_gbp() -> float:
    """Sum of stakes placed since UTC midnight today."""
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    total = 0.0
    if not AUDIT_LOG_PATH.exists():
        return 0.0
    try:
        with AUDIT_LOG_PATH.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("ts", "").startswith(today):
                    continue
                if r.get("status") != "placed":
                    continue
                total += float(r.get("stake_gbp", 0))
    except Exception:
        pass
    return total


# ============================================================================
# Approval workflow (Telegram /approve <request_id>)
# ============================================================================

_APPROVAL_DIR = Path(r"C:\Dev\odds\data\approvals")
_APPROVAL_DIR.mkdir(parents=True, exist_ok=True)


def _await_approval(req: OrderRequest, timeout_s: int = APPROVAL_TIMEOUT_SECONDS
                    ) -> bool:
    """Send Telegram alert with order details + request_id. Block until either
    a file `approvals/{request_id}.approved` appears or timeout.

    The Telegram bot (when implemented two-way) writes the .approved file on
    /approve <id>. For now, the operator can manually `touch` the file.
    """
    body = (
        f"⚠️ APPROVAL REQUIRED\n"
        f"Request: {req.request_id}\n"
        f"Venue: {req.venue.value}\n"
        f"Market: {req.market_label[:80]}\n"
        f"Side: {req.side.value} @ {req.price}\n"
        f"Stake: £{req.stake_gbp:.2f}\n"
        f"Rationale: {req.rationale[:200]}\n\n"
        f"To approve: send `/approve {req.request_id}` to the bot\n"
        f"OR create file: data/approvals/{req.request_id}.approved\n"
        f"Timeout in {timeout_s}s = auto-deny."
    )
    alert(body)
    flag = _APPROVAL_DIR / f"{req.request_id}.approved"
    deny_flag = _APPROVAL_DIR / f"{req.request_id}.denied"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if flag.exists():
            flag.unlink(missing_ok=True)
            return True
        if deny_flag.exists():
            deny_flag.unlink(missing_ok=True)
            return False
        if killswitch.tripped():
            return False
        time.sleep(2)
    return False


# ============================================================================
# Venue routers — plug venue clients into the gateway
# ============================================================================

def _place_betfair(req: OrderRequest) -> OrderResult:
    if not VENUE_BETFAIR_ENABLED:
        return OrderResult(req, "denied_by_gate",
                           reason="GATEWAY_VENUE_BETFAIR != 1")
    try:
        from venues.betfair_client import BetfairClient
        bf = BetfairClient.from_env()
        if not bf.creds.ready():
            return OrderResult(req, "failed",
                               reason="Betfair creds not configured")
        if not bf.login():
            return OrderResult(req, "failed", reason="Betfair login failed")
        # Convert stake_gbp to BF size + place.
        # Betfair listMarketBook + placeOrders are in betfair_client; here
        # we'd call a not-yet-implemented place_order method. For now we
        # explicitly stub and audit so the framework is testable end-to-end.
        return OrderResult(req, "failed",
                           reason="Betfair place_order not yet wired — "
                                  "implement in betfair_client.py")
    except Exception as e:
        return OrderResult(req, "failed", reason=f"betfair: {e}")


def _place_smarkets(req: OrderRequest) -> OrderResult:
    if not VENUE_SMARKETS_ENABLED:
        return OrderResult(req, "denied_by_gate",
                           reason="GATEWAY_VENUE_SMARKETS != 1")
    try:
        from venues.smarkets_client import SmarketsClient
        sm = SmarketsClient.from_env()
        if not sm.creds.ready():
            return OrderResult(req, "failed",
                               reason="Smarkets creds not configured")
        if not sm.login():
            return OrderResult(req, "failed", reason="Smarkets login failed")
        # Smarkets price is %, not decimal odds
        # req.price expected as 0..1 (probability) — convert to %
        price_pct = req.price * 100 if req.price <= 1.0 else req.price
        # Stake in pence
        quantity_pence = int(round(req.stake_gbp * 100))
        side = "buy" if req.side in (Side.BACK, Side.BUY) else "sell"
        order = sm.place_order(
            contract_id=req.selection_id, side=side,
            price_pct=price_pct, quantity_pence=quantity_pence,
        )
        sm.logout()
        if order:
            return OrderResult(
                req, "placed", order_id=str(order.get("id")),
                fill_price=req.price, fill_size=req.stake_gbp,
                placed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        return OrderResult(req, "failed", reason="Smarkets rejected order")
    except Exception as e:
        return OrderResult(req, "failed", reason=f"smarkets: {e}")


def _place_polymarket(req: OrderRequest) -> OrderResult:
    return OrderResult(
        req, "denied_by_gate",
        reason="Polymarket execution not permitted from UK IP per "
               "Polymarket TOS + Polymarket geoblocks GB. Use Betfair "
               "or Smarkets equivalent.")


# ============================================================================
# Public API — single entry point for any trade
# ============================================================================

def place_order(req: OrderRequest) -> OrderResult:
    """Place an order through the gateway. Every safety gate is checked."""
    # 1. Killswitch
    if killswitch.tripped():
        result = OrderResult(req, "denied_by_gate",
                             reason=f"killswitch tripped: {killswitch.reason()}")
        _audit({**asdict(req), **asdict(result), "stake_gbp": req.stake_gbp})
        return result

    # 2. Per-trade cap
    if req.stake_gbp > PER_TRADE_CAP_GBP:
        result = OrderResult(req, "denied_by_gate",
                             reason=f"stake £{req.stake_gbp:.2f} > "
                                    f"per-trade cap £{PER_TRADE_CAP_GBP:.2f}")
        _audit({**asdict(req), **asdict(result), "stake_gbp": req.stake_gbp})
        return result

    # 3. Daily cap (cumulative since UTC midnight)
    today_so_far = _stake_today_gbp()
    if today_so_far + req.stake_gbp > DAILY_CAP_GBP:
        result = OrderResult(req, "denied_by_gate",
                             reason=f"daily cap reached: today £{today_so_far:.2f} "
                                    f"+ this £{req.stake_gbp:.2f} > "
                                    f"£{DAILY_CAP_GBP:.2f}")
        _audit({**asdict(req), **asdict(result), "stake_gbp": req.stake_gbp})
        return result

    # 4. Approval gate for trades >= APPROVAL_THRESHOLD_GBP
    needs_approval = (req.requires_approval
                      or req.stake_gbp >= APPROVAL_THRESHOLD_GBP)
    if needs_approval:
        if not _await_approval(req):
            result = OrderResult(req, "denied_by_gate",
                                 reason="approval timed out or denied")
            _audit({**asdict(req), **asdict(result),
                    "stake_gbp": req.stake_gbp})
            alert(f"❌ DENIED  request {req.request_id} {req.market_label[:60]}")
            return result
        # Re-check killswitch after approval (operator may have tripped it)
        if killswitch.tripped():
            result = OrderResult(req, "denied_by_gate",
                                 reason=f"killswitch tripped during approval: "
                                        f"{killswitch.reason()}")
            _audit({**asdict(req), **asdict(result),
                    "stake_gbp": req.stake_gbp})
            return result

    # 5. Route to venue
    if req.venue == Venue.BETFAIR:
        result = _place_betfair(req)
    elif req.venue == Venue.SMARKETS:
        result = _place_smarkets(req)
    elif req.venue == Venue.POLYMARKET:
        result = _place_polymarket(req)
    else:
        result = OrderResult(req, "failed",
                             reason=f"unknown venue: {req.venue}")

    # 6. Audit + position book on success
    _audit({**asdict(req), **asdict(result), "stake_gbp": req.stake_gbp,
            "venue": req.venue.value, "side": req.side.value})

    if result.status == "placed":
        try:
            import positions
            positions.open_position(
                market_id=req.market_id,
                token_id=req.selection_id,
                side="BUY" if req.side in (Side.BACK, Side.BUY) else "SELL",
                size=req.stake_gbp,
                entry_px=req.price,
                validator=req.signal_id or "manual",
                market_label=req.market_label[:80],
                venue=req.venue.value,
                notes=req.rationale[:200],
            )
        except Exception as e:
            sys.stderr.write(f"[gateway] positions update: {e}\n")
        alert(f"✅ FILLED {req.venue.value} {req.side.value} £{req.stake_gbp:.2f} "
              f"@ {req.price} {req.market_label[:60]}")
    return result


def get_today_stake_gbp() -> float:
    return _stake_today_gbp()


def remaining_daily_cap_gbp() -> float:
    return max(0.0, DAILY_CAP_GBP - _stake_today_gbp())


# ============================================================================
# CLI for quick testing (paper mode)
# ============================================================================

def _cli():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", required=True, choices=[v.value for v in Venue])
    ap.add_argument("--market-id", required=True)
    ap.add_argument("--selection-id", required=True)
    ap.add_argument("--side", required=True, choices=[s.value for s in Side])
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--stake-gbp", type=float, required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--rationale", default="")
    args = ap.parse_args()
    req = OrderRequest(
        venue=Venue(args.venue),
        market_id=args.market_id,
        selection_id=args.selection_id,
        side=Side(args.side),
        price=args.price, stake_gbp=args.stake_gbp,
        market_label=args.label, rationale=args.rationale,
    )
    result = place_order(req)
    print(json.dumps(asdict(result), indent=2, default=str))


if __name__ == "__main__":
    _cli()
