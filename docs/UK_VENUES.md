# UK-legal venue map (May 2026)

Synthesis of the parallel research agent's findings + the trade gateway architecture for a UK-resident systematic operator.

## TL;DR

- **You can't legally execute on Polymarket from the UK.** Treat it as a read-only signal source.
- **Three UKGC-licensed exchanges** are the primary execution venues: Smarkets, Betfair, Matchbook.
- **Smarkets is the recommended primary venue** for any sharp/algo operation: 1% Pro commission, no premium-charge, sharp-friendly, modern API, low minimum stake.
- **Betfair** for size and US-politics depth, accepting the Expert Fee (20-40% above £25k profit).
- **All three are tax-exempt** as gambling under UK law (HMRC SAIM2080).
- **Crypto / commodity / weather / macro markets have no UK-legal retail venue.** That edge surface is unaddressable for you.

## Tier-1: UKGC-licensed exchanges

| | **Smarkets** | **Betfair Exchange** | **Matchbook** |
|---|---|---|---|
| UKGC licence | ✓ | ✓ | ✓ |
| Consumer protection | Full | Full | Full |
| Public API | REST + WebSocket | REST + Stream API | REST + Stream API |
| Auth | OAuth2 password grant | App key + session | API token |
| Cost to start | Free | £299 one-off live key | Free dev tier |
| Commission | 2% base, **1% Pro tier** | 2% base | 2% UK |
| **Premium / Expert charge** | **NONE** | 20% £25-100k, 40% above | NONE |
| Min stake | £0.05 | £2 | £1 |
| UK politics depth | Strong, often tightest | Highest | Thin |
| US politics depth | Decent, smaller than PM | Largest UK source | Minimal |
| Sports liquidity | Strong | Excellent | Racing + US sports |
| Sharp-friendly | **Yes — explicit** | Tolerant up to Expert Fee threshold | Low-medium restriction |
| Tax | Gambling-exempt | Gambling-exempt | Gambling-exempt |
| Built into odds | `venues/smarkets_client.py` ✓ | `venues/betfair_client.py` ✓ | not yet |
| Recommended for | **Primary venue** for systematic profit | Size + US politics | Diversification (later) |

## Tier-2: FCA-regulated spread betting

For categories that exchanges don't cover (financial thresholds, indices, crypto):

| | **IG Index** | **Spreadex** |
|---|---|---|
| Regulator | FCA | FCA + UKGC dual |
| API | IG Labs REST + Lightstreamer | Web/streaming, sports API needs verification |
| Markets | Financials, indices, FX, **politics spreads at major elections**, **crypto pro-only** | Sports spreads, financials, **UK political seats/turnout spreads**, novelty |
| Tax | Spread-bet exempt from CGT | Spread-bet exempt |
| **Crypto thresholds** | Available **professional clients only** (FCA retail ban Jan 2021) | N/A |

Crypto/BTC threshold equivalents to Polymarket exist via IG/Spreadex but require **FCA professional-client classification** (>£250k investible assets or relevant industry experience). High gate.

## Tier-3: UKGC sportsbooks (ignore for systematic)

Bet365, William Hill, Paddy Power, Sky Bet, Ladbrokes, Coral, BetVictor, BetFred, BoyleSports.

- No public APIs (paid odds aggregators only)
- All restrict sharp accounts within weeks
- Useful only for: occasional promo arb (early stage); odds reference

## Tier-4: Crypto-native / unlicensed (legal grey zones)

| | UK status | Notes |
|---|---|---|
| **Polymarket** | **API geoblocks GB** | Read-only signal source. TOS prohibits VPN. |
| **Kalshi** | US-only | CFTC DCM. Geoblocks UK. |
| **ForecastEx** | Via IBKR; **not offered to UK retail** | DCM. |
| **Limitless / Drift / Myriad** | On-chain, no enforcement, but unregulated | Same legal posture as Polymarket. |
| **Manifold** | Play-money | Real-money charity cashouts only. |

## Polymarket category coverage on UK-legal venues

| Polymarket strength | UK-legal alternative | Quality |
|---|---|---|
| Next UK GE / PM | Smarkets, Betfair | **Excellent** |
| US Presidential | Betfair > Smarkets | Strong (smaller depth than PM) |
| US Senate / House by state | Sparse on Smarkets/Betfair | **Gap** |
| Premier League / NFL / NBA | Betfair, Smarkets | **Excellent** |
| Oscars / Eurovision / awards | Betfair seasonal | Partial |
| **BTC/ETH price thresholds** | None retail; IG pro-only | **Gap** |
| **Weather** | None | **Gap** |
| **Geopolitics** | Patchy on Betfair | Largely **gap** |
| **Fed-rate / CPI / NFP** | None retail; IG financial spreadbet (FCA-restricted) | **Gap** |

## Architecture: trade_gateway as single entry point

Every order, regardless of source (CLI scanner, manual, MCP server, future bot), flows through `trade_gateway.py`:

```
┌────────────────────────────────────────────────┐
│ Source (CLI / manual / MCP server / scanner)   │
└──────────────────────┬─────────────────────────┘
                       │ OrderRequest
                       ▼
┌────────────────────────────────────────────────┐
│ trade_gateway.place_order():                   │
│   1. killswitch check                          │
│   2. per-trade cap (default £100)              │
│   3. daily cap (default £300)                  │
│   4. approval gate ≥£25 (Telegram /approve)    │
│   5. venue enable check                        │
│   6. route to venue client                     │
│   7. audit log every attempt                   │
│   8. position book on success                  │
│   9. Telegram filled/denied alert              │
└──────────────────────┬─────────────────────────┘
                       ▼
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   Smarkets      Betfair       Matchbook
   client       client          client
        │              │              │
        ▼              ▼              ▼
        UK-legal exchange execution
```

Polymarket as a venue option exists in the gateway but **always returns `denied_by_gate`** with reason "UK geoblock + TOS". This is intentional — keeps the schema uniform while enforcing the legal line.

## Configuration env vars

```powershell
# Caps (default safer values shown)
setx GATEWAY_PER_TRADE_CAP "100"
setx GATEWAY_DAILY_CAP "300"
setx GATEWAY_APPROVAL_THRESHOLD "25"

# Venue gates — must explicitly enable to allow live trades
# (default: 0 = paper-mode, all trades return denied_by_gate)
setx GATEWAY_VENUE_BETFAIR "0"
setx GATEWAY_VENUE_SMARKETS "0"

# Smarkets creds (for Smarkets execution)
setx SMARKETS_USERNAME "..."
setx SMARKETS_PASSWORD "..."
setx SMARKETS_API_KEY "..."  # optional, higher rate limits

# Betfair creds (for Betfair execution)
setx BETFAIR_APP_KEY "..."
setx BETFAIR_USERNAME "..."
setx BETFAIR_PASSWORD "..."
```

## MCP server: AI-driven trading with safeguards

`scripts/mcp_server/server.py` exposes the gateway to Claude Code as MCP tools. Tools available:

- `status` — gateway state, caps, today's spend, killswitch
- `get_polymarket_quote` — read-only PM quote
- `run_scanner` — invoke any scanner
- `place_order` — gateway-checked order placement
- `list_positions`, `pnl_summary`
- `halt` — trip killswitch

**Every place_order call still flows through the gateway**, so all 9 safety checks (killswitch, caps, approval gate, venue gate, audit) apply identically whether the call comes from a human CLI or the AI.

To install:
```powershell
pip install mcp
# Then add to Claude Code's MCP settings file pointing at:
#   python -m mcp_server.server
# in C:\Dev\odds\scripts\
```

## Recommendations

For a UK-resident systematic operator with a $10k bankroll:

1. **Open Smarkets account** (free, instant). Get OAuth creds. Set `SMARKETS_*` env vars but leave `GATEWAY_VENUE_SMARKETS=0` for paper trial.
2. **Apply for Betfair app key** (free, ~3 day approval). Set `BETFAIR_*` env vars but leave gate at 0.
3. **Keep Polymarket read-only forever** for your jurisdiction. It's the world's deepest signal source; just don't trade it directly.
4. **Run the system in paper-mode for 30 days**. Capture signals via the backtest harness (`backtest_validator.py`).
5. **After 30 days**: run `python backtest_validator.py decide`. Per-validator decision table tells you which scanners earn `ENABLE LIVE`.
6. **Enable one venue at a time**: flip `GATEWAY_VENUE_SMARKETS=1` first, run with default £25 approval threshold for 2 weeks, then loosen if PnL is positive.
7. **MCP server is optional** — only valuable if you want to converse with the AI about trades rather than running CLI commands. The safety gates are identical either way.

The goal is to never have an AI place a trade that bypasses the gateway. The gateway is the single chokepoint, regardless of caller.
