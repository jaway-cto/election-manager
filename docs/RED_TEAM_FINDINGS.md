# Red-team findings — Polymarket arbitrage system

Consolidated output of 4 parallel agents (adversarial, cost forensics, refinements, new theories) interrogating the system at C:\Dev\odds\ as of 2026-05-09.

The headline: **as built, two of the six scanners are net negative-EV** for a UK solo operator at $10k bankroll. They were going to lose money. Specific fixes shipped in this iteration are listed at the bottom.

---

## 1. Cost stack — the hidden 60% haircut

Polymarket V2 fees confirmed (Mar 2026 schedule):

| Category | Taker rate | Peak fee at p=0.50 |
|----------|-----------|---------------------|
| Geopolitical | 0.000 | $0 (fee-free) |
| Sports | 0.030 | $0.75 / $100 |
| Finance | 0.040 | $1.00 / $100 |
| Politics | 0.040 | $1.00 / $100 |
| Tech / Mentions | 0.040 | $1.00 / $100 |
| Culture (Eurovision, awards) | 0.050 | $1.25 / $100 |
| Economics / Weather | 0.050 | $1.25 / $100 |
| Crypto | 0.072 | $1.80 / $100 |

NegRisk basket arbs additionally pay a **2.04% multi-leg conversion surcharge** per the docs.

UK-specific drags: **24% CGT on USDC disposals** (~2pp on net edge), **FX vol ~0.7pp on 1-week holds**, GBP→USDC ramp **0.5–1.0%** each cycle.

**True net edges per $1k deployed (worked example):**

| Edge | Gross | Net after full cost stack |
|------|-------|--------------------------|
| WTI < $85 SELL @ 62c | 12.4pp | **+8.2%** ✅ |
| Eurovision Finland SELL | 8.5pp | **+5.1%** ✅ |
| BTC dip $75k SELL | 7.5pp | **+4.2%** ✅ |
| Tail-decay (geopolitical, fee-free) | 7.0pp | **+4.3%** ✅ |
| **negRisk 4-leg basket @ 4.2pp** | 4.2pp | **+0.5%** ❌ unprofitable |
| **negRisk basket < 8pp gross** | varies | **net negative** ❌ |
| **Crypto edges < 7.5pp gross** | varies | **net negative** ❌ |

---

## 2. Adversarial findings — top 5 expected $-loss vectors

Ranked by expected $ loss × probability on a $10k bankroll:

| # | Failure mode | Probability | Expected loss / yr | Mitigation |
|---|--------------|-------------|--------------------|----|
| 1 | LP-rewards `mm_score` ranks adverse-selected markets first | ~95% if used | **$8–18k** | DISABLED until score inverted + per-market $50 cap |
| 2 | Tail-decay past-deadline auto-execute on substring-only subjectivity filter | ~70% | **$3–11k** | Require positive whitelist of resolution-source patterns, human ack |
| 3 | negRisk basket "Other"-leg phantom edge (sum < 1.0 because we filtered the augmenter) | ~80% on flagged events | **$1.5–3k** | Include "Other"/"Another" leg in basket sum, not skip it |
| 4 | UMA oracle attack/mis-resolution on geopolitical markets | 1–3% per controversial position | **$1–3k** | Ban geopolitics + war + election-fraud markets entirely |
| 5 | HMRC reclassification: gambling-exempt → trading income | 15–25% if profits >£10k | 30–40% of profits | Get chartered-accountant opinion before scaling, set aside 30–40% provision |

**Most vulnerable scanners to adverse selection (ranked):**
1. LP rewards (constant negative selection)
2. Tail-decay past-deadline (you fill *because* a sharp is dumping pre-dispute)
3. negRisk basket (last leg moves; you fill the sticky leg the bots wouldn't touch)
4. PM × Betfair cross-venue (the slow side is slow for a reason)

---

## 3. Refinements — top 10 by ROI

| # | Improvement | Impact | Hours |
|---|-------------|--------|------|
| 1 | WebSocket book deltas in `validator_core.py` | scan 60s → 3s; -30% abort rate | 8 |
| 2 | Resolution-criteria classifier for tail-decay | -50% FP | 6 |
| 3 | Multi-leg simultaneous depth check in negRisk | -70% FP | 3 |
| 4 | Cross-scanner conviction merger | +20bps avg | 2 |
| 5 | Two-way Telegram (`/halt /positions /pnl /take`) | captures 3-5 fast-fading edges/wk | 6 |
| 6 | Price-velocity filter | -25% FP on news-driven markets | 3 |
| 7 | Backtest harness with strict timestamp gating | meta — confirms which scanners have real edge | 10 |
| 8 | Bankroll split (fast pool / slow pool) | prevents tail-decay drawdown blocking basket arbs | 2 |
| 9 | Watchdog + heartbeat-driven restart | removes silent-death failure mode | 4 |
| 10 | Post-only ladder for size > $200 | saves ~200bps taker fee on ~40% fills | 5 |

Items 1+2+3+10 alone: ~50% FP reduction across the three highest-volume scanners + 200bps fee savings + 20× speedup. **~22 engineer-hours total.**

---

## 4. New theories — top 5 orthogonal edges to add

Scored as edge × accessibility × orthogonality (max 125):

| # | Idea | Score | Hours |
|---|------|-------|-------|
| 1 | **PM-internal synthetic decomposition** (Trump nominee vs primary states joint) | 100 | 12 |
| 2 | **Calendar-spread theta harvester** (sell short-dated YES vs long-dated YES on same outcome) | 100 | 10 |
| 3 | Cold-open MM on freshly-listed markets (capture spread tightening 5-15c → 1c) | 80 | 15 |
| 4 | Cross-venue Manifold/Kalshi divergence trades | 64 | 12 |
| 5 | Macro pollers (BLS/EIA/USDA/WASDE/Fed) | 64 | 20 |

Recommendation: Calendar-spread (#2) is the cleanest Week 5 build — reuses prices-history infra, purely internal (no new venues/auth), captures a flow no current scanner sees.

---

## 5. Behavioural rules added to PLAYBOOK

1. **For the next 90 days, run every scanner read-only.** Log signals + would-have-been P&L. Compare to *actual* settled prices 30 days later. Only enable execution on a scanner once realised P&L is >70% of paper P&L.
2. **Hard ban on geopolitics/war/election-fraud markets** until UMA dispute history reviewed.
3. **Per-category minimum gross edge** (per cost stack):
   - geopolitical: 3pp (fee-free, one-side)
   - sports: 4.5pp
   - finance/politics/tech: 5.5pp
   - culture/economics/weather: 6.5pp
   - crypto: 7.5pp
   - negRisk basket: 8pp gross AND ≥2pp per leg
4. **Get chartered-accountant tax opinion before realising £10k+ profit.** Set aside 30-40% provision until written.
5. **Tail-decay execute requires human-typed confirmation of resolution-source URL** before order placement. Hard-cap 3 manual approvals per day.
6. **LP rewards quote-plan disabled** until score inverted (favor low vol/liq AND high holding-rewards) + per-market $50 cap.

---

## Fixes shipped in this iteration

(Implemented immediately following these findings.)

- Per-category fee model in `validator_core.py` with `min_edge_for_category()` enforcing the playbook thresholds
- `tail_decay_scanner.py`: removed past-deadline auto-execute default; added positive resolution-criteria whitelist; reduced `--execute` default cap by 80%
- `negrisk_scanner.py`: now includes "Other/Another" augmenter leg in basket sums (no longer filters it); enforces 8pp gross AND 2pp/leg minimums
- `lp_rewards_scanner.py`: `mm_score` formula inverted to favor low vol/liq markets with holding-rewards; per-market quote cap dropped to $50; warning printed when used
- Geopolitical / war / election-fraud markets blacklisted across all scanners
- FX + CGT drag added to net-edge computation in `validator_core.evaluate_market()`

---

## What's NOT shipped yet (future iterations)

- WebSocket book deltas (refinement #1) — biggest speed/accuracy win still pending
- Two-way Telegram (refinement #5)
- Backtest harness with strict timestamp gating (refinement #7)
- All 5 new theory builds (synthetic decomposition, calendar-spread theta, cold-open MM, Manifold/Kalshi cross-venue, macro pollers)

These remain in the backlog and would form Week 5+ if the user chooses to continue.
