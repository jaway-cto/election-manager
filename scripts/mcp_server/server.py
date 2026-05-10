"""
MCP server exposing the odds toolchain to Claude Code.

When configured in Claude Code's MCP settings, this lets the AI:
  * Fetch live odds from Polymarket / Betfair / Smarkets (read-only, safe)
  * Run scanners and surface signals
  * Place orders via the safety-gated trade_gateway (with caps + Telegram approval)
  * Inspect / update the position book
  * Toggle the killswitch

ALL trading goes through trade_gateway.py, so per-trade caps, daily caps,
killswitch, and approval gates all apply equally regardless of whether
trades are initiated by:
  * a CLI scanner with --execute --live, or
  * an AI calling place_order via this MCP server

To install:
    pip install mcp
Then add to Claude Code's MCP settings (~/.claude/mcp.json or similar):
    {
      "mcpServers": {
        "odds": {
          "command": "python",
          "args": ["-m", "mcp_server.server"],
          "cwd": "C:\\Dev\\odds\\scripts",
          "env": {
            "GATEWAY_VENUE_SMARKETS": "0",
            "GATEWAY_VENUE_BETFAIR": "0"
          }
        }
      }
    }

Initially leave GATEWAY_VENUE_* = "0" so trades are dry-runs. Flip to "1"
explicitly per venue once you've vetted everything end-to-end.
"""
from __future__ import annotations
import asyncio
import json
import sys
from dataclasses import asdict
from typing import Any

# The MCP SDK ships an Anthropic-supported reference implementation.
# If it's not installed, this server prints install instructions and exits.
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    sys.stderr.write(
        "MCP SDK not installed. Run:\n"
        "  pip install mcp\n"
        "Then re-launch the server.\n"
    )
    sys.exit(1)

# Project imports
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from validator_core import gamma_event, get_quote, parse_clob_token_ids
from trade_gateway import (
    OrderRequest, place_order, Venue, Side,
    PER_TRADE_CAP_GBP, DAILY_CAP_GBP, APPROVAL_THRESHOLD_GBP,
    VENUE_BETFAIR_ENABLED, VENUE_SMARKETS_ENABLED,
    get_today_stake_gbp, remaining_daily_cap_gbp,
)
import killswitch
import positions

server = Server("odds")


# ============================================================================
# Tool implementations
# ============================================================================

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="status",
            description="Show gateway status: killswitch, caps, today's spend, "
                        "venue enable flags. Always safe to call.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_polymarket_quote",
            description="Read-only: fetch current best bid/ask + spread for a "
                        "Polymarket market by event slug + group title (e.g. "
                        "'eurovision-winner-2026' + 'Finland').",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_slug": {"type": "string"},
                    "group_item_title": {"type": "string"},
                },
                "required": ["event_slug"],
            },
        ),
        Tool(
            name="run_scanner",
            description="Run one of the live scanners and return surfaced edges. "
                        "Read-only; does not place orders.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scanner": {"type": "string",
                                "enum": ["tail-decay", "negrisk",
                                         "lp-rewards", "wti", "crypto"]},
                },
                "required": ["scanner"],
            },
        ),
        Tool(
            name="place_order",
            description=(
                "Place a real-money order via the safety-gated trade_gateway. "
                "Order is rejected if killswitch is tripped, exceeds per-trade "
                f"cap (£{PER_TRADE_CAP_GBP}), exceeds daily cap "
                f"(£{DAILY_CAP_GBP}), or the venue is disabled. Orders ≥ "
                f"£{APPROVAL_THRESHOLD_GBP} require operator /approve via "
                "Telegram before execution. ALWAYS includes rationale. "
                "Polymarket venue is REJECTED (UK geoblock)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "venue": {"type": "string",
                              "enum": ["betfair", "smarkets"]},
                    "market_id": {"type": "string"},
                    "selection_id": {"type": "string"},
                    "side": {"type": "string",
                             "enum": ["back", "lay", "buy", "sell"]},
                    "price": {"type": "number",
                              "description": "Decimal odds (Betfair) or "
                                             "0..1 probability (Smarkets)"},
                    "stake_gbp": {"type": "number"},
                    "market_label": {"type": "string"},
                    "rationale": {"type": "string",
                                  "description": "Why this trade — required "
                                                 "for audit."},
                },
                "required": ["venue", "market_id", "selection_id", "side",
                             "price", "stake_gbp", "rationale"],
            },
        ),
        Tool(
            name="list_positions",
            description="Return all currently open positions with mark-to-market.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="pnl_summary",
            description="Per-validator realised PnL summary.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="halt",
            description="Trip the killswitch. Halts all trading immediately. "
                        "Safe to call from anywhere.",
            inputSchema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "default": "halted via MCP"},
                },
            },
        ),
    ]


def _text(payload: Any) -> list[TextContent]:
    if isinstance(payload, (dict, list)):
        return [TextContent(type="text",
                            text=json.dumps(payload, indent=2, default=str))]
    return [TextContent(type="text", text=str(payload))]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "status":
        return _text({
            "killswitch_tripped": killswitch.tripped(),
            "killswitch_reason": killswitch.reason(),
            "per_trade_cap_gbp": PER_TRADE_CAP_GBP,
            "daily_cap_gbp": DAILY_CAP_GBP,
            "approval_threshold_gbp": APPROVAL_THRESHOLD_GBP,
            "venue_betfair_enabled": VENUE_BETFAIR_ENABLED,
            "venue_smarkets_enabled": VENUE_SMARKETS_ENABLED,
            "today_stake_gbp": get_today_stake_gbp(),
            "remaining_daily_cap_gbp": remaining_daily_cap_gbp(),
        })

    if name == "get_polymarket_quote":
        slug = arguments["event_slug"]
        target_title = arguments.get("group_item_title")
        ev = gamma_event(slug)
        if not ev:
            return _text({"error": f"event not found: {slug}"})
        out = {"event": ev.get("title"), "slug": slug,
               "negRisk": ev.get("negRisk"), "markets": []}
        for m in ev.get("markets", []):
            git = m.get("groupItemTitle") or ""
            if target_title and target_title.lower() not in git.lower():
                continue
            yes_tok, _ = parse_clob_token_ids(m)
            q = get_quote(yes_tok) if yes_tok else None
            out["markets"].append({
                "id": m.get("id"),
                "groupItemTitle": git,
                "question": (m.get("question") or "")[:120],
                "bestBid": m.get("bestBid"),
                "bestAsk": m.get("bestAsk"),
                "live_book_bid": q.bid if q else None,
                "live_book_ask": q.ask if q else None,
                "live_spread_bps": q.spread_bps if q else None,
                "endDate": m.get("endDate"),
            })
        return _text(out)

    if name == "run_scanner":
        scanner = arguments["scanner"]
        if scanner == "tail-decay":
            from tail_decay_scanner import scan, report
            rows = scan(max_days=14, min_ask=0.92, max_ask=0.995)
            return _text({"n": len(rows),
                          "rows": [{"q": r.get("question"),
                                    "side": r.get("side_label"),
                                    "ask": r.get("ask"),
                                    "edge_pp": r.get("edge_pp"),
                                    "spread_bps": r.get("spread_bps"),
                                    "vol_24h": r.get("volume_24h"),
                                    "status": r.get("status"),
                                    "shape": r.get("shape")}
                                   for r in rows[:25]]})
        if scanner == "negrisk":
            from negrisk_scanner import scan
            rows = scan(min_edge_pp=1.0, min_volume_24h=10000)
            return _text({"n": len(rows),
                          "top": [{"event": r["event_title"],
                                   "n_legs": r["n_markets"],
                                   "buy_edge_pp": r["buy_basket_edge_pp"],
                                   "sell_edge_pp": r["sell_basket_edge_pp"],
                                   "implicit_other_pp": r["implicit_other"]*100,
                                   "vol_24h": r["volume24hr"]}
                                  for r in rows[:10]]})
        if scanner in ("wti", "crypto"):
            mod = "wti_validator" if scanner == "wti" else "crypto_validator"
            fn_name = "scan_wti_markets" if scanner == "wti" else "scan_crypto_markets"
            mod_obj = __import__(mod)
            rows = getattr(mod_obj, fn_name)()
            return _text({"n": len(rows),
                          "rows": [{"market": r.market[:80],
                                    "pm_yes": r.pm_yes, "fair": r.fair,
                                    "edge_bps": r.edge_bps,
                                    "edge_bps_net": r.edge_bps_net,
                                    "spread_bps": r.spread_bps,
                                    "skipped": r.skipped,
                                    "skip_reason": r.skip_reason,
                                    "action": r.action}
                                   for r in rows]})
        if scanner == "lp-rewards":
            from lp_rewards_scanner import scan as lp_scan
            rows = lp_scan(min_volume_24h=100, max_liquidity=100_000)
            return _text({"n": len(rows),
                          "top": [{"q": r["question"][:60],
                                   "yes_mid": r["yes_mid"],
                                   "spread_bps": r["yes_spread_bps"],
                                   "vol_24h": r["volume24hr"],
                                   "liq": r["liquidityClob"],
                                   "score": r["mm_score"]}
                                  for r in rows[:10]]})
        return _text({"error": f"unknown scanner: {scanner}"})

    if name == "place_order":
        req = OrderRequest(
            venue=Venue(arguments["venue"]),
            market_id=arguments["market_id"],
            selection_id=arguments["selection_id"],
            side=Side(arguments["side"]),
            price=float(arguments["price"]),
            stake_gbp=float(arguments["stake_gbp"]),
            market_label=arguments.get("market_label", ""),
            rationale=arguments["rationale"],
        )
        result = place_order(req)
        return _text(asdict(result))

    if name == "list_positions":
        rows = positions.list_open()
        return _text([dict(r) for r in rows])

    if name == "pnl_summary":
        return _text(positions.pnl_by_validator())

    if name == "halt":
        reason = arguments.get("reason", "halted via MCP")
        killswitch.trip(reason)
        return _text({"halted": True, "reason": reason})

    return _text({"error": f"unknown tool: {name}"})


# ============================================================================
# Main loop
# ============================================================================

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
