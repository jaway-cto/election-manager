# Untapped opportunities — outside-the-box research synthesis

Four parallel agents (sports/awards, matched-betting, wild-card, cross-asset hedging) interrogated the edge surface for a UK-resident operator. This document consolidates findings + ranks by realistic UK-legal accessibility × edge × buildability.

## The biggest finding overall

**Matched betting is the single most reliable UK-legal income stream we haven't built into the system.** Tax-free per HMRC BIM22015 (Graham v Green 1925, McMillan v HMRC 2020). Realistic income: £4-8k/yr for a diligent solo operator (~10 hrs/wk), decaying from £800-1,500/mo in month 1 to £150-400/mo by month 12 as accounts get restricted.

It's not glamorous, but it's mathematically guaranteed for the qualifying-bet conversion. £25 free bet ≈ £19 cash retention (76%) using Betfair lay. Multiply across 15-20 bookmaker accounts in the first month.

## Top 8 build candidates ranked

| # | Opportunity | UK-legal access | Realistic edge | Build hours | Why ranked here |
|---|-------------|-----------------|----------------|-------------|-----------------|
| 1 | **Matched betting infrastructure** | ✅ UKGC bookies + Betfair | £4-8k/yr realistic | 25-40h to automate beyond OddsMonkey | Highest sustained tax-free income for a solo UK operator |
| 2 | **PL Golden Boot model** | ✅ Betfair £1m+ liquidity | 150-600 bps × 38-week cycle | 8-10h | Highest-liquidity sports-prop on UK-legal venue; recycles Oscar pipeline |
| 3 | **EIA → IG oil spread-bet** | ✅ Spread-bet (FCA, tax-free) | 200-400 bps weekly | 12-15h | PM as signal source + FCA-regulated execution. Fully tax-clean. |
| 4 | **Tennis Grand Slam R1 upsets** | ✅ Betfair £500k/match | 200-400 bps on dogs >3.0 | 6-8h | Sackmann free open data. 4 slams/yr × 128 R1 matches. |
| 5 | **Ofgem price cap calculator** | Indirect via IG energy stocks | Deterministic 4×/yr | 4-6h | Mathematical formula publicly known. Trade SSE/Centrica spread-bet. |
| 6 | **Eurovision semi-final slot arb** | ✅ Betfair £50-150k semi markets | 400-700 bps | 14-18h | Academic-paper-backed (Spierdijk-Vellekoop 2009, Haan-Dijkstra 2005). Annual. |
| 7 | **F1 race-winner post-FP3** | ✅ Betfair £300-800k/race | 100-300 bps × 24 races/yr | 10-14h | fastf1 free telemetry. Sat afternoon → Sun race window. |
| 8 | **NBA MVP narrative-fade** | ✅ Betfair £200-500k | 300-800 bps | 12-16h | Jan-Mar window each year. RAPM model + voter-fatigue heuristic. |

## Key strategic insight from the cross-asset agent

**FCA-regulated spread-betting is the cleanest tax-free execution layer for UK retail.** Spread-bets are exempt from CGT and income tax (HMRC BIM22015). IG, CMC, Spreadex all retail-accessible without pro classification on:

- Equity events (single stocks, indices)
- FX (any major pair)
- UK political seats / turnout (Spreadex specials)
- UK energy spread-bet equivalents
- Commodity threshold (oil, natural gas)

**This is the missing execution layer.** Currently the system targets Smarkets/Betfair (gambling exchange) — but for financial-derivative-flavoured Polymarket markets (Fed rates, CPI, equity events, FX, oil/gas, BTC for pro-classified), the right hedge venue is IG or Spreadex, not Betfair.

## What's blocked / out of reach

| Strategy | Why blocked |
|----------|-------------|
| BTC ↔ Deribit digital options | FCA crypto-derivative ban PS20/10; pro classification required |
| BTC ↔ CME MicroBTC futures via IBKR | Same — FCA classes crypto-referencing as restricted |
| BTC ↔ IG crypto spread-bet | Pro-only since Jan 2021 |
| Polymarket execution from UK | Geo-blocked + TOS prohibits VPN |
| Snapshot DAO governance leak (capture-path) | Polymarket execution still required; Limitless/Myriad alternatives untested |

The crypto/commodity threshold edge surface (genuine 3-8pp basis vs Deribit) is **unreachable for a UK retail operator** unless you pursue FCA-pro classification (>£500k portfolio or 1yr+ industry experience).

## Specific concrete trades / tools to build

### Tier 1 — build immediately (highest ROI)

#### A. **Matched betting orchestrator** (`scripts/matched_betting/`)
- Pulls offers from OddsMonkey API (subscriber tier) + scrapes alternative-aggregator sources
- Auto-computes optimal lay stake at Betfair given each free-bet offer
- Tracks per-account lifetime status to predict restriction risk
- Logs to existing `positions.sqlite` with `validator='matched-betting'` so it shows up in `decide` table
- Realistic ROI: £300-700/mo first 6 months
- Build: 25-40 hours

#### B. **PL Golden Boot model** (`models/pl_golden_boot.py`)
- xG data from FBref / Understat (free, scraped weekly)
- Per-player Poisson-Gamma posterior on remaining-fixture goals
- Fixture difficulty + penalty-taker status as features
- Output: implied probability for each top-10 player; compare to Betfair Top Goalscorer market
- Build: 8-10 hours (recycles Oscar pipeline)

#### C. **EIA-to-IG-oil pipeline** (`scripts/eia_pipeline/`)
- Wed 10:30 ET poller of EIA Petroleum Status Report
- Tue 4:30pm ET poller of API survey (proxy for EIA)
- IG WTI spread-bet client (`venues/ig_client.py`)
- Signal: when API Tuesday print diverges from consensus by >threshold, alert + size IG position
- Build: 12-15 hours

### Tier 2 — build after Tier 1 proven

#### D. **Tennis Grand Slam R1 Elo** (`models/tennis_r1.py`)
- Sackmann ATP/WTA repo as data source
- Surface-adjusted Elo with Glicko uncertainty
- Auto-fires 4 weeks before each Grand Slam
- Compare to Betfair R1 dog prices >3.0 (where Sackmann shows +3% ROI historical)
- Build: 6-8 hours

#### E. **Ofgem price cap calculator** (`models/ofgem_cap.py`)
- Pulls ICE NBP gas + EFA power forwards (free delayed)
- Computes price cap formula per Ofgem methodology v1.27
- 4 announcements/year (Feb, May, Aug, Nov)
- Output: directional position on Centrica/SSE via IG spread-bet
- Build: 4-6 hours

#### F. **Eurovision slot-position scanner** (`models/eurovision_slot.py`)
- Pulls running order from eurovision.tv (announced ~5 days before semis)
- Applies slot-effect model: 1-3 = -8pp, 14-17 = +6pp, closing = +9pp
- Compares to Betfair semi-final qualifier prices
- Annual May firing
- Build: 14-18h

### Tier 3 — defer (cool but lower ROI)

- F1 fastf1-based model (decent but only 24 cycles/yr)
- NBA MVP (US-overnight execution awkward for UK)
- Snapshot DAO scanner (interesting but no clean UK execution)
- Spanish-wire LatAm election lag (rare events)

## Venue clients to add to `venues/`

Currently have: `betfair_client.py`, `smarkets_client.py`. Need:

| Venue | Auth | Markets covered | Status |
|-------|------|-----------------|--------|
| **IG Index** | OAuth + REST | UK equity, FX, indices, political spreads | Not started — Tier 1 priority |
| **Spreadex** | Login + scraping | UK politics, sports spreads, novelty | Not started — Tier 2 |
| **CMC Markets** | Web API | FX, indices, single stocks | Tier 3 |
| **Matchbook** | API token | Sports, occasional politics | Tier 3 |

The IG client unlocks #3 (EIA pipeline), #5 (Ofgem), and any future financial-event hedge.

## Realistic combined annual income (UK solo operator, £10k bankroll)

If all Tier 1 + Tier 2 are built and run for a year:

| Stream | Year 1 estimate |
|--------|-----------------|
| Matched betting | £4,000 |
| PL Golden Boot model | £600-1,800 (one cycle) |
| EIA-to-IG-oil | £400-800 |
| Tennis R1 | £400-800 (4 cycles) |
| Ofgem price cap | £200-400 (4 fires) |
| Eurovision slot | £100-300 (one cycle) |
| **Total realistic Year 1** | **£5,700 - £8,100** |

Variance dominates. Year 2 would be lower as matched-betting decays but Tier 2 builds bear fruit.

## Critical caveat

These numbers assume **builds work as modelled**. The 90-day backtest harness is the gating step — every model output should be paper-tracked for 30+ days before any live capital. Realistic Year 1 might be 0-50% of the table above if the models don't replicate paper-mode performance.

## Recommendation: build order

1. **Add IG Index client** to `venues/` (foundation for #3, #5, future hedges)
2. **PL Golden Boot model** — quick recycling of Oscar pipeline, immediate signal
3. **EIA pipeline** — first real cross-venue (Polymarket-signal, IG-execution) trade
4. **Matched betting orchestrator** — separate from arbitrage scanner; runs in parallel as a tax-free income stream
5. **Tennis R1 + Ofgem** — Tier 2 expansions

This is roughly 70-100 engineer-hours total. The MCP server already routes everything through `trade_gateway` so adding new venue clients is a 1-day exercise once IG OAuth is mapped.

Tell me which tier you want to start with.
