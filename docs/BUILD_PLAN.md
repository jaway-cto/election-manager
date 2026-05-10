# Build plan — Tier 1 + Tier 2 (UK-legal income stack)

## What we're building, in order

### Phase 1 — Foundations (engineer-hours: 6)

#### 1.1 — `venues/ig_client.py` (3-4 h)
The missing FCA-regulated spread-betting venue. Unlocks every "PM signal → IG hedge" trade.

**API surface** (IG Labs documented at labs.ig.com):
- POST `/session` — login with username/password/api_key, returns CST + X-SECURITY-TOKEN
- POST `/session/refresh-token` — keep alive
- DELETE `/session` — logout
- GET `/markets/{epic}` — market info (current bid/ask, deal sizes, expiry)
- GET `/marketnavigation` — navigate hierarchical market tree
- GET `/positions` — current open positions
- POST `/positions/otc` — place order (deal direction BUY/SELL, deal size, level for LIMIT, currency code)
- GET `/accounts` — balance + margin

**Auth model**: 3-step. POST with `x-ig-api-key` header → response gives `CST` and `X-SECURITY-TOKEN` cookies-as-headers, valid 6 hours. Then every subsequent call needs both headers + the api-key.

**Test environment**: demo.ig.com (totally free, no funding needed). Production: api.ig.com.

**Tax**: every spread-bet is CGT-exempt (BIM22015). Only documenting; no logic difference vs CFD account.

**Methods I'll implement**:
```python
class IGClient:
    def login() -> bool
    def logout() -> bool
    def get_balance() -> dict
    def search_markets(query) -> list[dict]
    def get_market(epic) -> dict       # bid/ask, dealing sizes
    def get_prices(epic, resolution, num_points) -> list  # historical
    def list_positions() -> list[dict]
    def place_order(epic, direction, size, order_type='MARKET', limit_level=None, stop_level=None) -> str  # deal_id
    def close_position(deal_id) -> dict
```

**Integration**: `trade_gateway.py` adds `Venue.IG`, routing in `_place_ig()`. Fee handling: IG spread is the cost; commission free. Stake conversion: IG measures in £/point — one IG point typically = $0.01 of underlying for FX, 1 cent for equities, varies by epic.

#### 1.2 — Trade gateway extension (1 h)
Add `Venue.IG` enum + `_place_ig` router. Spread-bet stake sizing is in £/point not £-notional, so the gateway's `stake_gbp` conversion needs an `epic_metadata()` helper that converts £ stake → IG `size`.

#### 1.3 — `verify_setup.py` extension (0.5 h)
Add IG check section: `IG_USERNAME / IG_PASSWORD / IG_API_KEY` env vars + login smoke test if all set.

### Phase 2 — Quick income (engineer-hours: 12)

#### 2.1 — `models/pl_golden_boot.py` (5-6 h)
Premier League Top Goalscorer model. Recycles Oscar precursor pipeline.

**Inputs**:
- FBref player season stats (xG, xA, npxG, minutes) — scraped weekly
- Understat per-game xG (free, scrape)
- Fixture list (free, ESPN or fixturedownload.com)
- Penalty-taker assignments (manual seed; updates rare)

**Model**:
- Per-player remaining-fixture goal expectation = Σ(opponent_def_xGA × player_npxG_per_90 × probability_starting × minutes_per_90)
- Add penalty bonus = (fixture_penalties_per_match × is_penalty_taker)
- Poisson-Gamma posterior on remaining-season goals
- Top-1 probability = monte-carlo from each player's posterior

**Output**: per-player implied probability, comparable to Betfair Top Goalscorer market.

**Refresh cadence**: weekly (Monday after weekend's results).

#### 2.2 — Wire to dashboard + signal capture (1 h)
Add `pl_golden_boot.scan()` to `unified_arb_dashboard.py`. Signal payload: top mispriced player + edge_pp.

#### 2.3 — `models/tennis_r1.py` (4-5 h)
Tennis Grand Slam first-round upset model.

**Inputs**:
- Sackmann ATP+WTA repo (full match history + player ELOs maintained in repo, free)
- Surface multipliers (clay/grass/hard) from regression on his data

**Model**: Surface-adjusted Elo with Glicko-2 uncertainty. For each R1 match, return implied probability of dog winning.

**Output**: rows where dog odds > 3.0 AND model probability > implied probability + threshold.

**Refresh**: 4 weeks before each Grand Slam, auto-fires when Betfair lists R1 markets.

### Phase 3 — Matched betting orchestrator (engineer-hours: 18-25)

This is the biggest income stream. Architecture:

```
┌────────────────────────────────────────────────┐
│ Daily routine (semi-automated)                 │
└────────────────────┬───────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────┐
│ scripts/matched_betting/offer_sources.py       │
│   * Pulls from OddsMonkey API/RSS              │
│   * Pulls from Outplayed RSS                   │
│   * Optional: scrapes Bonusbank / r/matchedbetting│
│   * Filters by: account-not-restricted,         │
│     min_ev, supported-sports                    │
└────────────────────┬───────────────────────────┘
                     │ Offer dict: {bookie, sport, event, market, leg, free_bet_amount}
                     ▼
┌────────────────────────────────────────────────┐
│ scripts/matched_betting/lay_calculator.py      │
│   * Looks up matching market on Betfair        │
│     (event + selection name match)             │
│   * Pulls live Betfair best back/lay           │
│   * Computes optimal lay stake using:          │
│     SNR formula: lay = back × (B-1) / (L - 0.05)│
│     (or SR formula for stake-returned bonuses) │
│   * Computes net retention (typical 70-78%)    │
│   * Outputs: alert with exact lay stake +      │
│     liability + expected net retention         │
└────────────────────┬───────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────┐
│ Operator manually places bookie bet            │
│ (no public API for any UK book)                │
│ Then sends /confirm <offer_id> via Telegram     │
│ → orchestrator places lay automatically         │
└────────────────────┬───────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────┐
│ scripts/matched_betting/account_book.py        │
│   * SQLite: bookie_accounts(bookie, balance,   │
│     last_bet_date, restriction_state, ...)     │
│   * Restriction predictor: flag accounts at    │
│     risk based on bet pattern signals           │
└────────────────────────────────────────────────┘
```

**Key design choices**:

- **No bookie automation**. UK bookies have no APIs and active anti-bot measures (TLS fingerprinting, Cloudflare). Trying to automate would breach UKGC LCCP (multi-accounting prohibition) AND get caught fast. Bookie side stays manual; only the lay side auto-executes.

- **Two-step offer flow**: alert with calculated lay → operator confirms → auto-lay. This mirrors the existing `_await_approval()` pattern in `trade_gateway.py`. The approval gate already exists; matched betting reuses it.

- **Account book** is the same SQLite the rest of the system uses. New table `bookie_accounts` joined to `positions` via `validator='matched-betting'`.

- **Mug-betting** support: schedule random small bets on accounts to maintain "recreational" appearance (extends account longevity per AI Profit research). Each mug bet is logged but not lay-hedged — small expected loss accepted as cost of account preservation.

#### 3.1 — `scripts/matched_betting/lay_calculator.py` (3-4 h)
Pure logic. Given (back odds, free-bet stake, free-bet type SNR/SR, lay odds, commission), output:
- Optimal lay stake to equalise outcomes
- Liability required
- Net retention (% of free-bet face value)

#### 3.2 — `scripts/matched_betting/offer_sources.py` (4-5 h)
- OddsMonkey RSS pull (subscribers get an authenticated feed URL)
- Outplayed RSS pull
- Manual offer ingestion via CSV / Telegram message
- Deduplication
- Filter by account restriction state

#### 3.3 — `scripts/matched_betting/account_book.py` (3-4 h)
SQLite schema:
```sql
CREATE TABLE bookie_accounts (
    id INTEGER PRIMARY KEY,
    bookie TEXT NOT NULL,        -- 'Bet365', 'WilliamHill', etc.
    username TEXT,                -- not stored unencrypted in production
    balance_gbp REAL,
    deposited_total_gbp REAL DEFAULT 0,
    withdrawn_total_gbp REAL DEFAULT 0,
    realised_pnl_gbp REAL DEFAULT 0,
    last_bet_date TEXT,
    restriction_state TEXT,       -- 'open' | 'soft_restricted' | 'gubbed' | 'closed'
    notes TEXT,
    opened_at TEXT,
    closed_at TEXT
);
CREATE TABLE bookie_bets (
    id INTEGER PRIMARY KEY,
    account_id INTEGER REFERENCES bookie_accounts(id),
    offer_id TEXT,
    bet_type TEXT,                -- 'qualifier' | 'free_bet_use' | 'mug_bet' | 'reload'
    stake_gbp REAL,
    odds REAL,
    selection TEXT,
    placed_at TEXT,
    settled_at TEXT,
    outcome TEXT,                 -- 'won' | 'lost' | 'void' | 'pending'
    settlement_gbp REAL,
    lay_position_id INTEGER REFERENCES positions(id),  -- the Betfair lay
    notes TEXT
);
```

#### 3.4 — `scripts/matched_betting/restriction_predictor.py` (2-3 h)
Heuristic ML on stored bets to predict per-account restriction risk.

Features per account:
- Bet count last 30d
- Always-best-price ratio (took offered odds within 60s of price publishing)
- Round-stake ratio (round £-amounts vs irregular)
- Promo-only ratio (bets only on promoted markets)
- Withdrawal speed (immediate after wins vs holding balance)
- In-play ratio (in-play bets are recreational signal)

Output: predicted weeks until restriction. When prediction <2 weeks, alert "consider rotating to lower-priority bookie".

#### 3.5 — `scripts/matched_betting/orchestrator.py` (4-5 h)
Main loop:
1. Pull current offers
2. For each, look up matching Betfair market
3. Compute lay stake + retention
4. Filter by retention > 70% AND account is open
5. Send Telegram alert with offer + recommended bookie + exact stake
6. On `/confirm <offer_id>` reply, place Betfair lay via existing `trade_gateway`
7. Log to bookie_bets + positions

### Phase 4 — Wire-up + dashboard (engineer-hours: 3)

- Add matched-betting as a section in `unified_arb_dashboard.py`
- Add `pl_golden_boot` and `tennis_r1` to dashboard
- Update `verify_setup.py` to check for ODDSMONKEY_RSS_URL, IG creds, etc.
- Update `SETUP.md` with new manual steps (next section)

### Total engineer-hours: 39-46

## Manual steps you need to do (parallel to my building)

These can be started today; I'll build while you work through them. I'll detail them after the build.

---

## Build sequence (concrete order)

I'll execute in this order so each piece is testable before the next builds on it:

1. **`venues/ig_client.py`** — IG Labs API wrapper (now)
2. **`trade_gateway.py` IG routing** — extend Venue enum (now)
3. **`models/pl_golden_boot.py`** — recycles Oscar pipeline (now)
4. **Matched-betting `lay_calculator.py`** — pure logic, easiest piece (now)
5. **Matched-betting `account_book.py`** — SQLite schema + helpers (now)
6. **Matched-betting `offer_sources.py`** — OddsMonkey RSS adapter (now)
7. **Matched-betting `orchestrator.py`** — main loop (now)
8. **`models/tennis_r1.py`** — Tennis R1 (after Phase 3 commit)
9. **Dashboard wire-up + verify_setup updates** (final)

I'll commit after each module so you have rollback points if anything goes wrong.

## Decisions I've made for you

To avoid pinging you every 30 seconds:

- **Matched betting orchestrator: semi-automated** (you place bookie bet manually, system places Betfair lay automatically on `/confirm`). Full bookie automation is not safe legally OR technically.
- **OddsMonkey is the offer source** rather than building offer-discovery from scratch. Their oddsmatcher is the industry standard; £20/mo is non-negotiable cost.
- **PL Golden Boot uses FBref + Understat, not paid xG vendor**. Free, slightly noisier, same edge.
- **IG demo first**, production once verified. I'll build for the demo endpoint and you flip the env var when ready.
- **All venue-side trade execution still flows through `trade_gateway`** so per-trade caps + killswitch + Telegram approval all apply uniformly.

If any of those choices is wrong for you, say so before I start. Otherwise I'm proceeding.
