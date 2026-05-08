# odds — election odds validator

Live tracker + market-calibrated model for UK political election outcomes. Surfaces edge between bookmaker prices and per-event model probabilities.

## Quick start

```powershell
# In whatever working directory you keep the live workbook (e.g. C:\Users\ringh\elections-2026)
python build_tracker.py
python populate_last_seats.py
python fetch_pollcheck.py

# Live: two terminals, two watchers
python poll_declared.py --watch --interval 180
python poll_markets.py  --watch --interval 60
```

## What you get

- A single Excel workbook with: Headline | Summary | per-region raw data | Markets | Market model
- Headline shows: declared seats per party (live, ~1-3 min lag from BBC) + predicted final per party (Polymarket-calibrated model)
- Markets tab shows: every Polymarket contract for the event, with edge flags where model disagrees
- Market model tab shows: per-council probability of each party winning most seats (P + expected seats per party)

## Read the docs

- [`CLAUDE.md`](CLAUDE.md) — full project context. Read this first.
- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — the model design
- [`docs/SOURCES.md`](docs/SOURCES.md) — data source ranking
- [`docs/ARBITRAGE.md`](docs/ARBITRAGE.md) — how to spot edge

## Status

Built during 2026 UK local elections (8 May 2026). Generalises to any election where: live results feed exists, pre-poll modelled forecast exists, prediction markets price the outcome.

Scotland Holyrood 2026 and Senedd 2026 are nearby candidates for extension; both have public Polymarket markets and similar BBC `wc-data` infrastructure.
