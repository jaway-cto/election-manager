# Polymarket arbitrage opportunities — ranked

Compiled 8 May 2026 from parallel agent analysis across Sports, Crypto, Politics, Macro, Geopolitics. Filtered for: free public data, programmatic access, high inference quality. Round 2 implementation agents produced working validators in `scripts/` — live numbers below override Round 1 estimates.

## Skew-corrected & contract-verified rankings (Round 3, post-correction)

After applying BTC put-skew correction (Deribit IV by strike, not ATM constant), most BTC edges collapse. WTI edges flagged for contract-spec verification before action.

| # | Trade | Face edge | Skew/spec corrected | Confidence | Action |
|---|---|---:|---:|---|---|
| 1 | Eurovision Finland — SELL YES @ 40¢ | +6.2pp | +6.2pp | MED | aggregator includes soft books; check Betfair Exchange line |
| 2 | BTC dip $75k — BUY NO @ 52.5¢ | -16.9pp | **-2.4pp** (skew-corrected) | MED | survives but small |
| 3 | BTC reach $95k — BUY YES @ 3.4¢ | +3.9pp | **+2.2pp** | MED | small absolute size, edge thin |
| 4 | WTI dip $85 — SELL YES @ 64.5¢ | -13.4pp | TBD | LOW until verified | contract spec misread suspected |
| 5 | BTC reach $85k — BUY YES @ 47.5¢ | +13.7pp | **+0.3pp (DEAD)** | NONE | original ATM-IV model overstated; skew-corrected = fair |
| - | BTC dip $65k — BUY NO | -5.8pp | **-0.2pp (DEAD)** | NONE | put IV 48% explains it |
| - | BTC dip $70k — BUY NO | -8.6pp | **-2.1pp (DEAD)** | NONE | put IV 42% explains it |
| - | GOP Nominee basket | "+50pp" | **0pp (DEAD)** | NONE | scanner bug + negRisk pattern |

The skew analysis (Deribit live: $65k put IV 48%, $70k 42%, $75k 38%, ATM 34%, $85k call 33%, $95k call 37%) confirmed put skew almost entirely explains the apparent downside-touch edges. Upside-touch edges at $85-90k similarly collapse because call IV ≈ ATM IV (no skew benefit). Only `$75k BUY NO` and `$95k BUY YES` survive, both at marginal +2pp.

**Lesson learned**: a constant ATM-IV input to GBM systematically over-states downside-touch edges in crypto. Always pull strike-specific IV from the smile.



| # | Market | PM | Fair (model) | Edge | Action | Validator |
|---|---|---:|---:|---:|---|---|
| 0 | **GOP Nominee 2028 basket overround** | 149.6% sum | 100% sum | **~50pp** | sell YES across basket | `cross_venue_scanner.py` |
| 1 | BTC dip $75k in May | 47.5% | 30.6% | **−16.9pp** | BUY NO | `crypto_validator.py` |
| 2 | BTC reach $85k in May | 47.5% | 61.2% | **+13.7pp** | BUY YES | `crypto_validator.py` |
| 3 | WTI dip $85 in May | 64.5% | 51.1% | **−13.4pp** | SELL YES | `wti_validator.py` |
| 4 | BTC reach $90k in May | 15.5% | 24.3% | +8.8pp | BUY YES | `crypto_validator.py` |
| 5 | BTC dip $70k in May | 15.5% | 6.9% | −8.6pp | BUY NO | `crypto_validator.py` |
| 6 | WTI dip $80 in May | 38.5% | 31.9% | −6.6pp | SELL YES | `wti_validator.py` |
| 7 | BTC dip $65k in May | 6.5% | 0.7% | −5.8pp | BUY NO | `crypto_validator.py` |
| 8 | BTC reach $95k in May | 3.4% | 7.3% | +3.9pp | BUY YES | `crypto_validator.py` |
| 9 | BTC hit $150k by Dec 31 | 9.5% | 5.8% | −3.7pp | BUY NO | `crypto_validator.py` |
| 10 | WTI ↑$110 in May | 43.5% | 40.5% | −3.0pp | SELL YES | `wti_validator.py` |
| 11 | WTI ↑$105 in May | 59.5% | 56.8% | −2.7pp | SELL YES | `wti_validator.py` |

**Note on #0 (GOP basket overround)** — verify resolution mechanics before trading. If candidates aren't mutually exclusive (e.g. multiple resolve YES if a non-listed candidate wins), the 149.6% is benign. If they are mutually exclusive, this is the largest single edge in the entire scan.

**Note on WTI** — every flagged strike is SELL YES. Pattern suggests either (a) traders pricing fatter geopolitical tails than OVX captures, or (b) systematic YES-buying liquidity premium. Discount edges by ~50% before trading.

**Note on BTC** — zero-drift GBM is symmetric. Real options market has negative skew (puts richer than calls). Downside-touch model edges (#1, #5, #7) are likely smaller than shown after skew adjustment; upside-touch edges (#2, #4, #8) are robust to skew correction or stronger.

## Cross-venue (Polymarket vs Kalshi) — currently no edge

Kalshi 2028 presidential markets exist (`KXPRESPARTY-2028-D`, `KXPRESPARTY-2028-R`) but show null yes_bid/ask/last — listed but untraded as of 8 May 2026. Scanner ready to surface arb the moment Kalshi posts quotes.



## Top 10 ranked

| # | Market | PM price | Fair value (model) | Edge | Domain | Data source | Effort |
|---|---|---:|---:|---:|---|---|---|
| 1 | **BTC ↑$105k in May** | 1¢ | ~5¢ | **5× edge** | Crypto | Deribit IV (free, no auth) | LOW |
| 2 | **WTI ≤$85 in May (NO)** | 39¢ | ~45¢ | **6pp edge** | Macro | FRED OVX + GBM | LOW |
| 3 | **BTC $150k by Dec 31** | 10¢ | ~14¢ | 1.4× edge | Crypto | Deribit IV | LOW |
| 4 | **WTI ≥$100 in May** | 78¢ | ~82% | 3-5pp | Macro | FRED OVX + GBM | LOW |
| 5 | **Pres 2028 Dem-sum vs Kalshi** | varies | Kalshi 0.61 | TBD | Politics | Kalshi public API + Polymarket Gamma | LOW |
| 6 | **Eurovision 2026 Winner** | varies | Betfair Exchange | 1-3pp | Sports | Betfair scrape / eurovisionworld.com | MED |
| 7 | **French Pres 2027** | varies | Smarkets + polling | TBD | Politics | Smarkets API + Wikipedia | MED |
| 8 | **House 2026 Dem 77%** | 77¢ | TBD | TBD | Politics | 538/Silver Bulletin generic ballot | MED |
| 9 | **NBA game-day moneylines** | varies | Bovada devig | 30-60s lag | Sports | Bovada JSON + ESPN | LOW (per game) |
| 10 | **UFC pre-fight (Strickland v Chimaev)** | varies | BestFightOdds | sharp money | Sports | BestFightOdds scrape | MED |

## Honourable mentions (modellable but small/risky)

- **Fed June No change @ 97%** — CME FedWatch says ~96%. ~1pp gap, inside transaction cost. Skip.
- **Hormuz traffic normalisation** — modellable from MarineTraffic AIS + EIA tanker data. 6/10 inferenceability. Worth investigating later.
- **Iran airspace closure** — NOTAM-driven, 5/10. Niche.

## Skip entirely

- **5m / 15m crypto up-down** — microstructure noise, EMH dominates, fees + spread eat any theoretical edge unless you co-locate as maker.
- **US-Iran peace deal ladder** ($89M largest market) — pure sentiment + Trump-tweet-driven volatility, 2/10 modellability. Treat as news-trading instrument, not probability problem.
- **Aliens, regime fall, US invades Iran, uranium custody, hantavirus pandemic** — 1-3/10 inferenceability. No defensible model.
- **FIFA World Cup 2026** — Polymarket has deepest WC liquidity globally; often LEADS traditional books. No edge.
- **Trump posts / Elon tweets weekly** — settled or near-settled by listing time, no useful edge window.

## Internal-inconsistency flags

Worth investigating in Round 2:

1. **Iran-related markets sum to >97%**: P(US invades before 2027) = 23% + P(permanent peace by Dec 31) = 74% leaves only ~3% for muddle-through. At least one over-priced — likely the peace leg given $89M crowd-flow chasing the narrative.
2. **Polymarket Dem-2028 sum vs Kalshi**: needs computing — sum Newsom 25 + AOC 8 + others, compare to Kalshi `KXPRESPARTY-2028-D` 61%. Direct cross-venue arb if mismatch >2pp.
3. **Polymarket Pres-2028 individual vs nominee markets**: Newsom-as-president 17% vs Newsom-as-nominee 25% implies 68% conditional general-election win — high vs implied Vance-as-president 19% which presupposes GOP party 38-39%.

## Verified free APIs (no auth, no payment)

| API | Endpoint | What it gives |
|---|---|---|
| Polymarket Gamma | `https://gamma-api.polymarket.com/events?slug=<slug>` | All Polymarket prices |
| Polymarket CLOB | `https://clob.polymarket.com/` | Order book depth |
| Kalshi | `https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=<series>` | All Kalshi market data |
| Smarkets | `https://api.smarkets.com/v3/events/?type_domain=politics` | Politics market list + IDs |
| Deribit | `https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option` | BTC/ETH options chain + IV |
| FRED | `https://api.stlouisfed.org/fred/series/observations?series_id=DCOILWTICO` | WTI spot, OVX (needs free API key) |
| Binance | `https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT` | Crypto spot, free |
| Bovada | `https://www.bovada.lv/services/sports/event/coupon/events/A/description/<sport>` | NBA/UFC/F1 odds |
| ESPN | `https://site.api.espn.com/apis/site/v2/sports/<sport>/<league>/scoreboard` | Game state + DK-sourced odds |

## Decision priorities

**Build first (highest edge, lowest effort)**:
1. Crypto threshold validator — Deribit IV → Black-Scholes touch probabilities → compare to Polymarket. (#1, #3 above)
2. WTI barrier validator — FRED OVX → reflection-principle touch math → compare. (#2, #4)
3. Polymarket↔Kalshi cross-venue scanner — direct price compare for political 2028 markets. (#5)

**Build second (more effort, real edge)**:
4. Eurovision Betfair scraper. (#6)
5. NBA game-day Bovada-vs-Polymarket scanner. (#9)

**Defer or skip**: everything else.
