# Validators API & Data-Source Reference

Complete map of every API used (or available) for our Polymarket arbitrage scanners. All free / minimal-auth. Last updated 2026-05-09.

## Architecture

```
                   ┌─────────────────────────────────────┐
                   │     unified_arb_dashboard.py        │
                   │  (orchestrator: ranks all edges)    │
                   └─────────────────────────────────────┘
                                    │
       ┌─────────┬─────────┬────────┼────────┬─────────┬─────────────┐
       ▼         ▼         ▼        ▼        ▼         ▼             ▼
   crypto_v   wti_v    macro_v   sports_v  euro_v   french_v    nba_v
       │         │         │        │        │         │            │
       ▼         ▼         ▼        ▼        ▼         ▼            ▼
   Deribit    OVX      CME       The     evw.com   Smarkets    Bovada
   Binance   Yahoo  FedWatch    Odds      etc.       API       ESPN
                                 API
       └────────┴─────────┴────────┴────────┴─────────┴────────────┘
                                    │
                                    ▼
                  ┌─────────────────────────────────────┐
                  │        validator_core.py            │
                  │   (CLOB book, spread, OI, edge)     │
                  └─────────────────────────────────────┘
                                    │
                                    ▼
                            Polymarket APIs
                  Gamma  ◄──►  CLOB  ◄──►  Data API
```

## Polymarket APIs (free, primary)

| API | Base URL | Auth | Rate limit | Used for |
|-----|----------|------|-----------|----------|
| Gamma | `https://gamma-api.polymarket.com` | None | 500 req/10s (`/events`) | Market discovery, search, metadata |
| CLOB read | `https://clob.polymarket.com` | None | 1500 req/10s (`/book`) | **Real bids/asks**, midpoint, spread, prices-history |
| Data API | `https://data-api.polymarket.com` | None | 1000 req/10s | Open interest, volume, holders |
| CLOB trade | same | L2 (HMAC) | — | Order placement (we don't use) |

### Critical CLOB endpoints we use

| Endpoint | Returns | Key fields |
|----------|---------|-----------|
| `/book?token_id=X` | Full order book | `bids`, `asks`, `timestamp` |
| `/midpoint?token_id=X` | Mid price | `mid` |
| `/spread?token_id=X` | Bid-ask spread $ | `spread` |
| `/prices-history?market=X&interval=1h&fidelity=60` | OHLC time series | `history`: [{`t`, `p`}, ...] |

### Why CLOB matters

Gamma `outcomePrices` returns last-trade snapshot — can be stale by hours, doesn't reveal spread. CLOB `/book` exposes the actual executable order book. **Every validator now uses CLOB best-ask (BUY side) or best-bid (SELL side) to compute realistic edges**, with spread as a hard execution gate.

Verified live: many BTC threshold markets show >20pp spread on outright snapshots that look "tight" via Gamma `outcomePrices`. Our spread filter correctly rejects these.

---

## Per-validator data sources

### `crypto_validator.py`
- **Spot + IV**: Deribit (`https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option`) — no auth, IV smile by strike
- **Spot fallback**: Binance (`https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT`)
- **Markets**: Polymarket BTC threshold events
- **Model**: Reflection-principle GBM one-touch with strike-specific IV (post-skew correction)

### `wti_validator.py`
- **Spot**: Yahoo Finance `CL=F` futures (`query1.finance.yahoo.com/v8/finance/chart/CL=F`)
- **Spot fallback**: FRED `DCOILWTICO` (requires `FRED_API_KEY` env var)
- **IV**: Yahoo `^OVX` (CBOE Crude Oil VIX)
- **Model**: GBM reflection-principle barrier touch
- **Caveat**: contract spec verification still pending — flagged LOW confidence

### `macro_validator.py`
- **Primary**: CME FedWatch JSON tree (`cmegroup.com/services/fed-watch-tool/api/v3/probability-tree`) — currently 403-blocked from non-browser callers; we degrade gracefully
- **Fallback**: Fed Funds futures quotes + Gaussian heuristic for outcome probabilities
- **Markets**: Polymarket "Fed decision in [Month]" events
- **Status**: works when CME endpoint accessible; otherwise 0 edges flagged

### `sports_validator.py`
- **Primary**: The Odds API v4 (`api.the-odds-api.com/v4`) — requires free API key (`THE_ODDS_API_KEY` env var; 500 req/month free tier)
- **Coverage** (with key): NFL, NBA, MLB, NHL, EPL, Champions League, La Liga, Bundesliga, Serie A, MLS, ATP, WTA, UFC
- **Model**: Multi-book devig consensus (averages decimal odds across 3-10 sportsbooks, removes overround)
- **Matching**: Polymarket Gamma `public-search` for both team names

### `nba_validator.py`
- **Lines**: Bovada free JSON (`bovada.lv/services/sports/event/coupon/events/A/description/basketball/nba`)
- **Status**: ESPN public API (`site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard`)
- **Model**: Two-way devig from Bovada moneylines

### `eurovision_validator.py`
- **Aggregator**: eurovisionworld.com odds page (multi-book average) — scraped
- **Fallback**: hardcoded snapshot from 8 May 2026
- **Model**: Overround removal via proportional scaling

### `french_pres_validator.py`
- **Cross-venue**: Smarkets v3 API (`api.smarkets.com/v3/markets/23982189/quotes/`) — free, no auth, basis-point quotes
- **Polling**: Wikipedia opinion-poll page (best-effort regex scrape)
- **Model**: Devig + cross-venue compare

---

## Validator output schema

Every validator now produces `EdgeRow` objects (defined in `validator_core.py`):

```python
@dataclass
class EdgeRow:
    validator: str           # "crypto", "wti", "sports", etc.
    market: str              # human-readable market label
    market_id: str           # Polymarket market ID (for OI lookup)
    yes_token: str           # CLOB token ID for the YES outcome
    pm_yes: float            # executable PM price (CLOB ask if buying)
    fair: float              # model probability (0-1)
    edge_bps: float          # (fair - pm_yes) * 10000
    action: str              # BUY YES | SELL YES | -
    spread_bps: float        # bid-ask spread in bps of mid
    oi_usd: float            # open interest (when available)
    volume_24h_usd: float    # 24h volume
    confidence: str          # HIGH / MED / LOW (liquidity-derived)
    attempt: float           # ranking score: |edge|/spread * sqrt(OI/500k)
    attempt_label: str       # STRONG / GOOD / MARGINAL / SKIP
    skipped: bool            # True if execution-filter rejected
    skip_reason: str         # human-readable reason if skipped
```

---

## Filter gates (execution-viability)

Every flagged edge passes through `should_skip()` before being marked actionable:

| Gate | Default | Reasoning |
|------|---------|-----------|
| `max_spread_bps` | 500 (5pp) | Hard wide-market rejection |
| `max_spread_vs_edge` | 0.5 | Spread must be < 50% of edge magnitude |
| `min_oi_usd` | 50,000 | Below = anyone could be the only counterparty |
| `min_book_size_shares` | 100 | Best ask must have > 100 shares of depth |
| `max_book_age_seconds` | 300 | Reject quotes older than 5 minutes |

These can be tuned per-validator via `FilterParams`.

---

## Other free APIs not yet integrated

Mapped during research (`HOW_TO_USE_POLYMARKET.md` siblings) but not wired up:

| Domain | API | Effort | Why deferred |
|--------|-----|--------|--------------|
| Crypto futures | Binance/Bybit/OKX perpetuals (basis trading) | MED | Overlaps crypto_validator; basis pricing needs care |
| FX | ExchangeRate.host (zero auth) | LOW | Polymarket FX markets are illiquid — likely no edges |
| Commodities | Commodities-API, Twelve Data | MED | Free tiers limited; futures pricing model needed |
| Bonds / yields | FRED + Treasury Fiscal Data | LOW | Few PM markets here; defer until they exist |
| Tennis | The Odds API tennis_atp_singles | LOW | Rolls in once `sports_validator` has API key |
| Golf | The Odds API + Data Golf | LOW | Same |
| Horse racing | The Racing API (paid) | HIGH | Paid only, skip |

---

## Setup

```powershell
# 1. Install deps
pip install requests scipy

# 2. (Optional) Free FRED key for WTI fallback
$env:FRED_API_KEY = "your-fred-key"

# 3. (Optional) Free The Odds API key for sports
$env:THE_ODDS_API_KEY = "your-odds-api-key"

# 4. Run unified dashboard
cd C:\Dev\odds\scripts
python unified_arb_dashboard.py
```

Output goes to `C:\Dev\odds\docs\DASHBOARD.md` and console.

---

## Backtest workflow

```powershell
# Single signal
python backtest_validator.py --token <CLOB_TOKEN> --signal-ts 1715000000 \
    --action "BUY YES" --model-fair 0.52 --hold-days 7

# Batch via JSONL (one signal per line)
python backtest_validator.py --signals signals.jsonl --hold-days 7
```

Aim: backtest current/past flagged edges, target win rate >55% (50% = no edge baseline). Sub-50% means model needs revisiting.

---

## Maintenance notes

- **CLOB cache**: 5-second TTL on books and OI. Tight loops won't hammer Polymarket.
- **Rate limits**: At default 60s polling, we use <1% of CLOB allowance.
- **Slug rot**: Polymarket renames events occasionally. Use `gamma_search()` rather than hardcoded slugs where possible.
- **CME blocking**: `cmegroup.com/services/fed-watch-tool` returns 403 to non-browser callers. May need to add browser-mimicking headers or a ResiliumProxy-style workaround. As of 2026-05-09, validator gracefully degrades to no Fed signal rather than failing.
