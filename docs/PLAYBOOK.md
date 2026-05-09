# Polymarket Arbitrage Playbook — May 2026

Synthesis of 4 ideation agents (cross-venue, model-driven, structural, information) + 2 web-validation passes + a planning agent. Reconciled against live evidence as of 2026-05-09.

The numbers here are **realistic post-saturation** estimates for a single individual with code skills and ~$10k bankroll. Headline returns from the brainstorm phase have been discounted where research showed bots/professional MMs already extract most of the edge.

---

## Reality check from Stage 2 web research

| Theme | Stage 1 headline | Validated reality |
|-------|------------------|-------------------|
| negRisk YES-overround | 30-80% APR | $29M already extracted by bots Apr-24 to Apr-25 ([source](https://medium.com/@navnoorbawa/negrisk-market-rebalancing-how-29m-was-extracted-from-multi-condition-prediction-markets-2f1f91644c5b)). Realistic solo: **5-15% APR**, lumpy. |
| UMA dispute window | 100%+ APR | OOV2 → Managed OOV2: only 37-address whitelist can propose. Dispute-only side viable but small. |
| Tail decay 97→100¢ | 25-60% APR | Validated. Bots sweep majors quickly; long-tail crumbs remain. **8-20% APR.** |
| Settlement-impossibility | 50-150% APR | Sports books auto-cancel at game start. Macro/political residual cases exist but tiny depth ($100-1k). **Low absolute $.** |
| LP rewards / Maker rebates | — | **CLOB v2 launched 2026-04-28 with active $1M liquidity rewards program.** Fresh opportunity. ([Polymarket announcement](https://help.polymarket.com/en/articles/14762452-polymarket-exchange-upgrade-april-28-2026)) |
| SCOTUS / NHC / FDA polling | 500-5000 bps × infrequent | Confirmed feasible. **Asymmetric, lumpy.** |
| AP Elections feed | free | Paid only. Use SoS scrapes / NYT undocumented JSON instead. |

**Net:** the most accessible solo opportunities turn out to be **(a) the brand-new CLOB v2 liquidity-rewards program**, **(b) information polling on slow-moving feeds (SCOTUS/NHC/FDA)**, and **(c) tail-decay sweep of the long tail**. The pure-mechanical structural plays are mostly bot territory.

---

## Top 5 build priorities

### #1 — CLOB v2 liquidity rewards (LP / maker rebates)
- **What:** Quote both sides inside the spread on rewards-eligible markets. Earn USDC daily regardless of fills.
- **Why this rank:** Brand new (28 Apr 2026), $1M program budget, niche markets the pros ignore are still wide-open. This is the freshest non-saturated edge in the entire validation pass.
- **Realistic APR on $10k:** 5-15% baseline, up to 25% with skilled inventory mgmt
- **Capital:** $5-25k working inventory (deeper = more rewards-eligible)
- **Build effort:** 25-40 hours (real market-making bot)
- **Deps:** Polymarket L2 API key + HMAC signing, inventory management logic, reward-program eligibility list (refresh daily)
- **First step:** Read Polymarket maker-rebates docs, list eligible markets via Gamma API, pick 5 niche markets (politics state-races, low-volume sports) where you can post $200-500 per side near mid without competing with pro MMs

### #2 — SCOTUS / NHC / FDA information poller
- **What:** Single async daemon polling SCOTUS opinions (Tue/Thu 10am ET sittings), NHC advisories (03/09/15/21 UTC), FDA press releases. On detection, alert + auto-pull matching PM market and check current price vs implied resolution.
- **Realistic APR:** 30-60% on deployed capital (lumpy, event-driven)
- **Capital:** $3-5k held ready, deploy on signal
- **Effort:** 25-30 hours
- **Deps:**
  - SCOTUS: poll `https://www.supremecourt.gov/opinions/slipopinion/25` every 10s 09:55-11:00 ET on sitting days
  - NHC: subscribe `https://www.nhc.noaa.gov/CurrentStorms.json` (no auth)
  - FDA: poll `https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml`
- **First step:** Build `pollers/` directory with 3 async pollers + Telegram alert sink. Run read-only for one full session week before adding auto-execution.

### #3 — Tail-decay + endgame redemption sweep
- **What:** Scan resolved or near-resolved markets for residual asks below 99.5¢ (long tail; majors are bot-swept in seconds). Buy + redeem at $1.00.
- **Realistic APR:** 8-20% on parked capital
- **Capital:** $5-15k (depth-limited per market)
- **Effort:** 8-12 hours
- **Deps:** CLOB book scan across all markets with `endDate < now + 7d` AND `closed=false`. Already partially in `validator_core.py`.
- **First step:** Add `tail_decay_scanner.py` — query `gamma /markets?closed=false&end_date_max=<now+7d>`, fetch CLOB books, find asks at 0.95-0.999 with confirmable resolution, buy automatically up to per-market cap.

### #4 — PM × Betfair Exchange cross-venue (politics + UK sports)
- **What:** Two-leg arbitrage. Where PM YES + Betfair LAY equivalent for same outcome leaves locked spread > fees, take both sides.
- **Realistic APR:** 25-50%
- **Capital:** $5k each leg
- **Effort:** 15-20 hours
- **Deps:** Betfair Exchange app key (free, ~3 day approval at developer.betfair.com), funded GBP account, event-mapping table maintained manually
- **Best opportunities:** US 2028 markets, UK general election, Champions League outrights, Eurovision (already proven 6-8pp gaps observed)
- **First step:** Apply for Betfair app key today. Build `venues/betfair_client.py`, manual CSV of top 20 PM↔Betfair pairs, run scanner for 1 week before sizing up.

### #5 — Award-show precursor model (Oscars, Emmys, Grammys)
- **What:** Bayesian model: BAFTA/Globes/SAG/DGA wins → Oscar probability per category. Run Jan-Mar each year.
- **Realistic APR:** 30-60% per cycle, ~$2-5k profit on $5k deployed in Oct-Mar window
- **Capital:** $2-5k
- **Effort:** 15-20 hours one-off (reusable annually)
- **Deps:** GoldDerby + Wikipedia precursor history, IMDb metadata
- **First step:** Build CSV of Best Picture winners 2000-2025 with each precursor's winner that year. Logistic regression baseline. Then expand to Director, Acting categories.

---

## Defer or skip (with rationale)

| Theme | Why skip |
|-------|----------|
| negRisk YES-overround basket | $29M already extracted; durable >3% baskets are seconds-scale and need private RPC to win |
| Polling-driven election models | Crowded by Silver/Morris/DDHQ. Edge thin vs effort. |
| Macro nowcasts (CPI/NFP) | CME FedWatch + consensus already near-efficient. Tiny edge. |
| Crypto perp basis / DEX-CEX | MEV bots and prop desks own this entirely. |
| Stablecoin depeg | Tail risk dominates; one bad bridge fill wipes a year of edge. |
| UMA dispute window | Whitelist proposers + Risk Labs internal monitoring. Realistic edge tiny. |
| In-play sports lag | PM in-play depth poor, racing bots. Skip. |
| Promo / sharp-vs-soft devig | Account limiting kills it within weeks. Not durable. |
| SGP decomposition, Asian handicap synthetics | Complex + account-limiting risk |
| Wikipedia death edits | Markets too thin, slightly ghoulish |
| Settlement-impossibility | Real but tiny — bundle into #3 sweeper instead of standalone |

---

## Cross-cutting infrastructure (build before any theme)

1. **Always-on poller daemon** — single async process, configurable per-source intervals. `python -m pollers.daemon` running under `supervisord` or Windows Task Scheduler.
2. **Telegram alert pipe** — free bot, two channels: `actionable-now`, `fyi`. Sub-second latency to phone.
3. **SQLite position book** — `positions(market_id, side, size, entry_px, hedge_venue, hedge_size, hedge_px, status, pnl_realised, opened_at, closed_at)`. Reconcile vs PM Data API nightly.
4. **Signed-order client** — extend beyond read-only. L2 HMAC headers per Polymarket docs. Test with $10 fills first.
5. **Kill switch** — env var `ODDS_TRADING_HALT=1` checked every loop. Telegram `/halt` command flips it.
6. **Daily PnL email** — mark-to-market vs CLOB mids, attribution by theme.

Effort: ~20 hours upfront, then maintenance only.

---

## 4-week build schedule (~50 hours total, part-time)

**Week 1 — Infrastructure** (12 hrs)
- Telegram bot, SQLite position book, kill switch (6 hrs)
- Signed-order PM client + $10 test fills (4 hrs)
- Tail-decay scanner v1, read-only (2 hrs)

**Week 2 — Themes #3 + start #1** (13 hrs)
- Tail-decay scanner with auto-execution + per-market caps (5 hrs)
- LP rewards eligibility scanner; pick 5 niche markets (4 hrs)
- First market-maker bot (post-only, no hedging yet) (4 hrs)

**Week 3 — Theme #2 pollers** (13 hrs)
- SCOTUS poller + holding-text parser (5 hrs)
- NHC `CurrentStorms.json` poller + advisory diff (4 hrs)
- FDA RSS poller (4 hrs)

**Week 4 — Themes #4 + #5** (13 hrs)
- Betfair API client + 20-market mapping CSV (8 hrs)
- Oscar precursor dataset + logistic baseline (5 hrs)

End of month: 5 themes live; #5 ready to deploy in autumn award-precursor window.

---

## Capital allocation ($10k bankroll)

| Bucket | Cap | Position size |
|--------|-----|---------------|
| #1 LP rewards (working inventory) | $4k | $200-500/side per market |
| #2 Info polling (held ready) | $2k | $300-800 per signal |
| #3 Tail decay | $1.5k | $50-150 per crumb |
| #4 PM × Betfair | $1k each leg | $200-500 per arb |
| #5 Awards (seasonal) | $1k | $200-400 per category |
| Cash buffer | $1.5k | event-drop opportunistic |

**Unit risk:** Max 2% of bankroll ($200) on any single market until 30 settled trades show edge. Then scale to 5% if Sharpe > 1.5.

**Scale-up trigger:** Realised PnL > $2k AND > 50 trades AND max drawdown < 15%. Then double bankroll from external funds.

**Hard stops:** Halve all unit sizes at 20% drawdown. Pause theme at 35%.

---

## Top 3 failure modes

1. **UMA mis-resolution on ambiguous markets.** Tail-decay (#3) assumes UMA settles correctly; markets with subjective language ("substantially complete", "primarily about") get disputed and lock capital for weeks. **Mitigation:** Hard-filter to markets with quantitative, public-data resolution criteria. Skip anything with subjective language in the rules.

2. **Adverse selection on info polls.** If a faster bot beats you to a SCOTUS/NHC/FDA signal by 5 seconds, you trade against a stale book — losses guaranteed. **Mitigation:** Always check best-bid/ask freshness (last-trade timestamp < 10s) before sending order. Use limit orders inside the spread, not market orders. Abort if book moved in the last 10s.

3. **Fees + slippage eating mechanical edges.** PM fees, Polygon gas, and CLOB slippage on thin books can flip a 2¢ edge negative. **Mitigation:** Bake full round-trip cost (fee + 1 tick slippage each side + gas) into edge calc before alerting. Require minimum 3¢ net edge after costs. Track realised vs expected slippage weekly; recalibrate.

---

## What we already have (reusable)

- `validator_core.py` — CLOB book/spread/OI fetching, edge classification, EdgeRow schema, FilterParams
- `unified_arb_dashboard.py` — parallel orchestrator + ranking by attempt score
- `backtest_validator.py` — replay against CLOB prices-history
- 5 working domain validators (crypto, wti, eurovision, french_pres, nba)
- `sports_validator.py`, `macro_validator.py` — placeholders for The Odds API + CME

The 4-week plan **extends** this base; we don't need to rebuild infrastructure.

---

## Validated free data sources (Stage 2b)

| Source | URL | Auth | Latency |
|--------|-----|------|---------|
| Wikipedia EventStream | `stream.wikimedia.org/v2/stream/recentchange` | UA only | <1s edit→stream |
| NHC CurrentStorms | `nhc.noaa.gov/CurrentStorms.json` | none | ~60s post-advisory |
| SCOTUS slip opinions | `supremecourt.gov/opinions/slipopinion/25` | scrape | 30-90s post-release |
| api.weather.gov | `api.weather.gov` | UA only | ~5000 req/hr |
| ECMWF Open Data | `data.ecmwf.int/forecasts/` (also AWS/GCP/Azure) | none | 6-8h post-run |
| FDA Press RSS | `fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml` | none | 1-10 min |
| SEC EDGAR | `efts.sec.gov/LATEST/search-index` | UA + 10/s cap | 1-10 min |
| Snapshot.org | `hub.snapshot.org/graphql` | 60/min free | ~5s post-vote |
| Federal Reserve | `federalreserve.gov/feeds/press_monetary.xml` | none | URL is predictable; HEAD-poll |
| Polymarket WS | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | none | <100ms |
| Bundeswahlleiterin | `bundeswahlleiterin.de/.../opendata/` | none | every few min on election night |
| France elections | `elections.interieur.gouv.fr` | none | 5-15 min lag |

AP Elections is the only paid-only source identified; we work around with state SoS scrapes + NYT undocumented JSON.

---

_This is a living document. Update with realised PnL by theme monthly. Re-rank quarterly._
