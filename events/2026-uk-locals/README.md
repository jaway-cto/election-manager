# 2026 UK Local Elections — event instance

Polling: Thursday 7 May 2026
Declarations: Friday 8 May 2026
Working directory used during the count: `C:\Users\ringh\elections-2026\`

## Event-specific data files

- `councils_data.py` — 145 councils (144 England + 1 Welsh ward by-election + 0 Scotland)
  - 6 county councils, 32 London boroughs, 32 metropolitan boroughs, 18 unitaries, 48 districts, 8 late-additions, 1 Welsh by-election
- `last_seats.json` — derived per-party seats won at each council's last comparable election (2022 from HoC + 2021 from OCD for counties)
- `pollcheck.json` — per-council pre-poll modelled projections (PollCheck MRP)
- `bbc_data.json` — most recent BBC live snapshot (regenerated each poll)
- `le2022.xlsx` — House of Commons Library 2022 local elections handbook (source for last_seats.json non-counties)
- `ocd_history.csv` — Open Council Data UK history (source for counties' 2021 baseline)

## Polymarket events for this election

Hardcoded in `scripts/poll_markets.py`:

- `2026-united-kingdom-local-elections-reform-wins-seats` — Reform seat thresholds (1400, 1600, 1800, 2000, 2200)
- `2026-united-kingdom-local-elections-labour-wins-seats` — Labour seat thresholds (300-700)
- `2026-united-kingdom-local-elections-conservative-wins-seats` — Conservative thresholds (300-600)
- `2026-united-kingdom-local-elections-green-wins-seats` — Green thresholds (500-900)
- `2026-united-kingdom-local-elections-party-winner` — most seats overall
- `2026-united-kingdom-local-elections-2nd-place` — second-most overall
- `will-reform-win-a-mayorship-in-the-2026-united-kingdom-local-elections` — Reform mayorship

## BBC pages used

- `https://www.bbc.co.uk/news/election/2026/england/results` — primary (`wc-data` scoreboard endpoint)
- `https://www.bbc.co.uk/news/election/2026/scotland/results` — Scotland (no council elections, scoreboard returns Holyrood — ignored)
- `https://www.bbc.co.uk/news/election/2026/wales/results` — Wales (only Newport ward by-election)
- `https://www.bbc.co.uk/news/election/2026/england/councils` — per-council list with winners

## Final state notes

(Fill in once the count completes overnight 8/9 May.)

- Final declared totals: Reform __ / Lab __ / LD __ / Con __ / Grn __ / Other __
- Final councils: __ Reform / __ Lab / __ LD / __ Con / __ Grn / __ NOC
- Polymarket "Reform 1st" closed at __% (opened ~99.5%, ranged ___)
- Polymarket "Reform ≥ 1600" closed at __%

## Lessons learned (post-count)

- (Populate after count finishes.)
