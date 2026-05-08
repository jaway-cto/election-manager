# Arbitrage / edge framework

How to use the tracker to identify mispricings between live markets and the model.

## Three categories of edge

### 1. Threshold ladder mispricing (Polymarket primary)

Polymarket prices laddered seat-count thresholds for each major party (e.g. Reform ≥ 1,400, ≥ 1,600, ≥ 1,800, ≥ 2,000). The Markets tab shows YES probability for each.

Edge appears when:

- **Live declared rate clearly clears or fails a threshold**, but the market price hasn't caught up.
- **The model's Polymarket-implied target moves** (because more council declarations shift `MARKET_IMPLIED` derivation) but a specific threshold market is slow to move.

How to spot it:

1. Look at Markets tab "DISAGREE" flag column (red highlight). It triggers when:
   - Rate-extrapolation projects party clears threshold but market YES < 40%; or
   - Rate-extrapolation projects party fails threshold but market YES > 60%.
2. Cross-check against Market model tab aggregate seat totals (bottom of tab) vs `MARKET_IMPLIED`. Where they differ by >50 seats, threshold ladders for that party may be mispriced.

Caveats:
- Polymarket liquidity per threshold contract is thin ($5-30k). Edge identification is more reliable than execution.
- The market often has information our model doesn't (specific council exit polls, politician statements). Disagreement is a signal to investigate, not to trade blindly.

### 2. Order-of-finish mispricing (party-winner / 2nd-place markets)

Polymarket prices "Will X be 1st" and "Will X be 2nd" per major party. These resolve based on final seat order.

Edge appears when:

- The Market model's per-party expected seats imply a clear order, but the market price gives meaningful weight to alternatives.
- Live declarations have shifted the order but the market hasn't repriced.

How to spot it:

1. Check Headline tab "Predicted final" column. Read off the order.
2. Check Markets tab for "Will X win the most" / "Will X win the second-most" entries.
3. If model gives Lab > LD by 400 seats but market prices LD-2nd at 15%, that 15% is rich. Conversely if model is closer to a tie, market < 5% is too tight.

Live example (May 2026): early afternoon, Lab 2nd was 86%. By evening with London declarations narrowing Lab/LD gap, market rose to 95.1%. Model's 99% never moved because the predicted seat margin was always comfortable.

### 3. Per-council mispricing (theoretical)

Per-council markets do not currently exist. If Smarkets / Betfair launches single-council "winning party" or "control" markets in future cycles, the Market model tab's per-party P columns are directly usable as fair-value reference.

Build pattern:

1. Take Market model P(Con), P(Lab), ..., P(Oth) for the council.
2. Convert to decimal odds: `odds = 1 / P`.
3. If a market price > model odds by >5%, edge exists for backing.
4. If market price < model odds by >5%, edge for laying.

The Monte Carlo gives full distribution, so each-way and place-style markets can also be priced.

## When NOT to take a model-vs-market disagreement seriously

- **Method 4 (Market model) was calibrated to Polymarket aggregate**. So tautologically it shouldn't disagree with Polymarket on aggregate. If it does (>5%), the calibration may have gone stale (`MARKET_IMPLIED` needs refreshing).
- **Methods 1-3 disagreements with market are routine** — those methods are uncalibrated, simpler, less informed than the market. Disagreement means our simple method is missing something, not that the market is wrong.
- **Thin-volume markets** (<$5k matched) — bid-ask wide, prices don't reflect consensus. Treat as noise.
- **The market has private information** — exit polls, politician briefings, count-floor reporting that arrives via PA wire before public sources. Specifically: if a market suddenly moves and the reason isn't obvious in your declared data, the market is probably ahead of you, not wrong.

## Edge size you should require

Heuristic, not advice:

- **<3% gap**: noise. Stay out.
- **3-7% gap**: investigate. Look for the reason. Often you'll find an information asymmetry not a mispricing.
- **7-15% gap**: real candidate. Re-derive your model number from scratch — don't trust automation alone.
- **>15% gap**: something is wrong. Either market is illiquid, contract terms differ from your assumption, or your model has a bug. Deep dive before action.

## Workflow during a count night

1. Tracker auto-refreshes. Watch Headline for declared % to climb past key thresholds (25%, 50%, 75%).
2. When a major council declares (Birmingham, Sheffield, county council), check Market model row for that council — was the modal winner correct?
3. If model was significantly wrong on a recent declaration, swing has shifted — wait for the next 5-min poll cycle, then check whether national swing in Per-region tabs has updated.
4. After each big declaration, scan Markets tab edge flags. Note any new "DISAGREE" rows.
5. Update `MARKET_IMPLIED` in `market_model.py` if Polymarket has moved >5pp on Reform since last edit. Re-run.

## Auditing the system

`pr-review-toolkit:silent-failure-hunter` agent ran a comprehensive audit during May 2026. Findings documented in commit history. Recurring concerns:

- Race between two pollers — addressed via `xlsx_lock.py`.
- HoC LAB+COOP mapping — not actually a bug (HoC pre-aggregates).
- County baseline = composition not seats-won — flagged, lived with.
- MC independence — flagged as future work.
- Hand-tuned `MARKET_IMPLIED` — flagged as future work.

Re-run the audit before any new event using the same agent template (see commit log).
