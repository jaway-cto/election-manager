# Data sources

Verified May 2026. Latency = time between Returning Officer declaration at the count venue and data being visible to a non-paying caller.

## Live results

| # | Source | Latency | Format | Access | Used? |
|---|---|---|---|---|---|
| 1 | Returning Officer (in-person) | T+0 | spoken | venue only | n/a |
| 2 | **PA Media wire** | 30-90 s | XML | paid (broadcaster contracts only) | no |
| 3 | **BBC News `wc-data`** | 1-3 min | JSON | free, unauth | **PRIMARY** |
| 4 | Sky / ITV News | 1-5 min | HTML | free, scrape | no |
| 5 | Reuters | 1-5 min | HTML/wire | free HTML | no |
| 6 | Council websites | 5 min – hours | HTML/CSV/PDF (varies) | free per-council | no — too disparate |
| 7 | Election Maps UK / Britain Elects | 5-30 min | tweets / HTML | free | no |
| 8 | Democracy Club API | hours – days | JSON | free, unauth | **fallback** |
| 9 | Wikipedia | 30 min – next day | HTML | free | no |

### BBC `wc-data` (primary)

Lightweight backend JSON used by their live results pages. ~3.5 KB per call vs 533 KB for the full HTML page.

```
GET https://www.bbc.co.uk/wc-data/container/scoreboard
  ?assetUri=/news/election/2026/england/results
  &dataProperty=scoreboard
  &service=news
  &year=2026
```

Returns `status.message` (e.g. "After 106 of 136 councils declared.") and `groups[0].scorecards[]` — each containing `title` (party label) and `score.dataColumns = [[councils_total, councils_change], [councillors_total, councillors_change]]`.

Per-council winners (England only) via:
```
GET https://www.bbc.co.uk/wc-data/container/az-list
  ?assetUri=/news/election/2026/england/councils
  &entities=councils
```
Returns 136 cards each with `title`, `href` (containing GSS code), and `winnerFlash` (null when pending; populated with `winnerPartyCode`, `flash` text, `prevWinnerPartyCode` once declared).

Implementation: `scripts/fetch_bbc.py`.

### Democracy Club API (fallback)

Volunteer-entered ward-level results. Used for per-ward seat counting and multi-mandate handling, not for headline declared count.

```
GET https://candidates.democracyclub.org.uk/api/next/results/?election_date=2026-05-07&page_size=500
```

Per-ballot `candidate_results[]` with `elected: true/false`, `party.ec_id`, `num_ballots`. We aggregate `elected=true` per (council slug, party) for the per-ward DECLARED block on region tabs.

Implementation: `scripts/poll_declared.py`.

## Pre-poll modelled forecast

| Source | What it provides | Free? | Used? |
|---|---|---|---|
| **PollCheck.co.uk** | Per-council central + low–high seat range, P(control change) | yes | **PRIMARY** |
| Election Maps UK | Defending seats + ward boundaries (no projections) | yes | no |
| YouGov MRP | Senedd 2026 only — not English locals | yes | no |
| Britain Elects | Headline national projection only | yes | no |
| Survation | Holyrood only | yes | no |

PollCheck pages: `https://www.pollcheck.co.uk/council-projections/<slug>/`. Slug = lowercase council name with underscores. 136 English councils covered. No JSON API — HTML scrape.

Implementation: `scripts/fetch_pollcheck.py` → `pollcheck.json`.

## Last election seats

| Source | Coverage | Format | Notes |
|---|---|---|---|
| **House of Commons Library** | 2021/2022/2023/2024/2025 — every GB council that polled that year | xlsx | Definitive: uses `Elected=1` flag per candidate. **PRIMARY for non-counties.** |
| **Open Council Data UK** | 2016–2025 every UK principal council | CSV | Post-election composition (not seats won). Used for **counties** (where HoC 2021 file isn't yet integrated). |
| Wikipedia | per-council per-year | HTML infobox | Reliable cross-check but per-council fetches. |

HoC 2022 file used: `local-elections-2022.xlsx` (3.9 MB). Direct URL is Cloudflare-gated against `curl`; needs `User-Agent: Mozilla/5.0` + `Referer` header.

Implementation: `scripts/populate_last_seats.py` → `last_seats.json`.

## Live betting markets

| Source | Coverage | API | Used? |
|---|---|---|---|
| **Polymarket** | Seat thresholds, party winner, 2nd place, Reform mayorship | Gamma API (free, JSON, no auth) | **PRIMARY** |
| Betfair Exchange | Winning party + 2nd place + head-to-heads. Best liquidity. | App key + session token required | no — auth-gated |
| Smarkets | Winning party only | Public REST | no |
| Oddschecker | High-street books aggregator | scrape only, Cloudflare | no |

Polymarket Gamma:
```
GET https://gamma-api.polymarket.com/events?slug=<event-slug>
```
Returns event with `markets[]` each having `outcomes`, `outcomePrices`, `volume`, `lastTradePrice`, `closed`, `active`. Prices update continuously; safe to poll every 60 s.

Implementation: `scripts/poll_markets.py`. Hardcoded slugs for the 7 relevant 2026-uk-locals events; update for new event groups.

## Per-council market odds

**Do not exist.** Confirmed against Smarkets, Betfair, Polymarket, Oddschecker, William Hill, Ladbrokes, Coral, Sky Bet, Paddy Power. Anyone presenting "per-council odds" is presenting a model, not a market price.

Available substitutes:
- PollCheck per-council projections (modelled, FPTP-aware)
- Our `market_model.py` per-council Monte Carlo (calibrated against Polymarket aggregate)
