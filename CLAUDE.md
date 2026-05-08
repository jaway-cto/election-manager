# odds — election odds validator + arbitrage scanner

## Purpose

This project is a framework for tracking UK political election results in real time, modelling expected outcomes, and comparing those projections against live betting market prices to surface mispricings (arbitrage candidates).

It was built during the 2026 UK local elections (polling 7 May, declarations 8 May) but is structured so the patterns can extend to any election where:

- A live results feed exists (returning officer declarations propagated through media)
- A pre-poll modelled forecast exists (e.g. PollCheck MRP)
- Liquid prediction markets price the outcome (Polymarket, Smarkets, Betfair)

The output is a single workbook with: declared seats per party (live) | calibrated model prediction | live market prices | per-council probabilities | edge flags where market and model disagree.

## What this is NOT

- Not a betting bot. It does not place trades. All market access is read-only public data.
- Not a per-council market scraper — per-council odds do not exist anywhere (verified, May 2026). Only national headline markets are priced.
- Not a paid-source aggregator. PA Media wire (the fastest source at ~30s) is paywalled and not used.

## Repository layout

```
odds/
  CLAUDE.md            ← this file (project context for any session)
  README.md            ← quick start
  docs/
    METHODOLOGY.md     ← model design + calibration logic
    SOURCES.md         ← data source ranking with latency + access
    ARBITRAGE.md       ← how to spot edge between market and model
  scripts/             ← reusable code
    poll_declared.py   ← orchestrator: pulls DC API, BBC, runs model, writes workbook
    poll_markets.py    ← Polymarket Gamma API poller (read-only)
    market_model.py    ← per-council Monte Carlo model, market-calibrated
    fetch_bbc.py       ← BBC News wc-data JSON fetcher (fastest free source)
    fetch_pollcheck.py ← one-shot PollCheck.co.uk scraper
    populate_last_seats.py ← derives last-election seats from HoC + OCD sources
    build_tracker.py   ← creates the .xlsx skeleton + region tabs
    xlsx_lock.py       ← cross-process file lock for concurrent watchers
  data/                ← shared reference data (currently empty; events carry their own)
  events/
    2026-uk-locals/    ← per-election instance
      councils_data.py
      pollcheck.json
      bbc_data.json
      le2022.xlsx
      ocd_history.csv
      ...
```

## How to run a tracker session

For an existing event (`events/2026-uk-locals/`):

```powershell
cd C:\Users\ringh\elections-2026   # working directory used during the actual count

# One-shot rebuild
python build_tracker.py            # creates xlsx skeleton from councils_data.py
python populate_last_seats.py      # derives last-election baselines (writes last_seats.json)
python fetch_pollcheck.py          # one-shot pre-poll forecast scrape (writes pollcheck.json)

# Live polling (start in separate windows, both run as background watchers)
python poll_declared.py --watch --interval 180   # council results: 3 min
python poll_markets.py  --watch --interval 60    # market odds: 60 s

# Each poll cycle: fetch BBC live data → fetch DC API → run market_model →
# write Headline + Summary + Markets + Market model tabs
```

## Concurrency

Two pollers write to the same workbook. Coordinated via `xlsx_lock.py`:

- File sentinel `council_tracker.lock`
- Stale-lock breaker after 120 s
- `save_with_retry()` handles Excel having the file open (PermissionError) — retries 6× with 2 s delay

Always close Excel before manual rebuilds to avoid clashes with the watcher.

## Key data sources, ranked by speed (verified May 2026)

1. **PA Media wire** — ~30-90 s lag from declaration. Paid (£££, broadcaster contracts only).
2. **BBC News `wc-data` JSON** — ~1-3 min lag. Free, unauth. `https://www.bbc.co.uk/wc-data/container/scoreboard?...` returns 3.5 KB JSON. **This is our primary live source.**
3. **Sky / ITV / Reuters** — ~1-5 min, free HTML, fragile to scrape.
4. **Council websites** — 5 min to several hours, no standard format.
5. **Election Maps UK / Britain Elects** — 5-30 min, manual curation.
6. **Democracy Club API** — hours to days. Volunteer-entered. Used as fallback / sanity check.
7. **Wikipedia** — 30 min to next day.

## Methodology in one paragraph

For each council we have: last-election seat counts (House of Commons Library 2022 dataset + Open Council Data 2021 for counties), pre-election composition (manual research), live declared seats (BBC `wc-data` + Democracy Club API), and a pre-poll modelled projection with a low–high seat range (PollCheck MRP). For undeclared councils, we calibrate PollCheck's central estimates so that the aggregate projected Reform total matches Polymarket's implied expected value (currently ~1,590) — bisection-solved per-party scaling factor on Reform alone, with per-council renormalisation absorbing the rest. Then a Monte Carlo (4,000 trials) per council samples each party's seats from N(calibrated_mean, σ) where σ derives from PollCheck's published low–high range, with a shared "Reform momentum" factor capturing within-council Con/Lab/Reform correlation. Argmax across trials gives P(party wins most seats in council). Aggregating expected seats across 144 councils and comparing to Polymarket's implied totals provides a sense check (currently within 50 seats on Reform).

## Why "arbitrage scanner"

The tracker surfaces three kinds of opportunity:

1. **Per-council mispricing** — only theoretical here, since per-council markets don't exist. If they appeared (e.g. via Smarkets headline events), the Market model tab would directly produce fair-value prices.
2. **National threshold mispricing** — Polymarket prices laddered seat-count thresholds (e.g. "Reform ≥ 1,800 seats"). The Markets tab shows YES probability + the model's expected total. When live data + PollCheck-implied tail pushes through a threshold while the market price hasn't moved, that's an edge.
3. **Order-of-finish mispricing** — "Lab to be 2nd" type markets. Live declared data combined with which councils are still pending updates the implied probability. When market price hasn't reflected new declarations, edge exists briefly.

See `docs/ARBITRAGE.md` for the framework.

## Critical caveats / known limitations

- **County FPTP threshold non-linearity**: live-swing model treats vote-share-to-seat-share as linear, which under-predicts Reform breakthroughs in shire counties. Market model corrects via PollCheck's FPTP-aware projections + Polymarket calibration. Pure swing-adjusted column should not be trusted on county outcomes.
- **PollCheck Monte Carlo independence**: party samples in each council are independent (with one shared Reform momentum factor). Real party-vote-share correlations are richer. Probabilities will be slightly miscalibrated near tossup councils.
- **Polymarket-implied expected value** is hand-derived from threshold ladders (50% point heuristic). When the market moves materially, manually update `MARKET_IMPLIED` in `market_model.py`.
- **Democracy Club lag**: do not use DC API as the headline declared figure — it lags BBC by 30-60% mid-evening. Use BBC `wc-data` for the headline.
- **No per-council market data**: confirmed across Smarkets, Betfair, Polymarket, Oddschecker, all UK bookmakers. Anyone offering per-council "odds" is offering a model, not a market price.

## Memory / facts about this project

- Workbook tabs (in order seen by user): Headline | Summary | England | Scotland | Wales | Markets | Market model
- Headline is the single view — declared (BBC live) + predicted final (Market model calibrated to Polymarket).
- Polymarket Gamma API is the cleanest live odds source: free, unauth, JSON, refreshes continuously.
- Reform calibration scaling factor is bisection-solved per cycle; expect ~2.2-2.5× for May 2026 locals.
