# How to use the Polymarket API for our odds-validator

Notes for using the Polymarket API stack in `C:\Dev\odds\`. Full endpoint reference saved at `POLYMARKET_API.md` (13k lines, 28 endpoints). Index at `POLYMARKET_API_INDEX.md`.

## Three APIs, three jobs

| API | Base URL | Auth needed? | What it gives us |
|---|---|---|---|
| **Gamma** | `https://gamma-api.polymarket.com` | None | Market discovery — events, markets, tags, search, sports |
| **CLOB** (read) | `https://clob.polymarket.com` | None for read endpoints | Real order books — bids, asks, midpoint, spread, last trade, history |
| **Data** | `https://data-api.polymarket.com` | None | Open interest, live volume, holder analytics, leaderboards |
| CLOB (trade) | `https://clob.polymarket.com` | L2 (API key + HMAC) | Order placement — we don't use, validator only |

**Important**: We've been using only Gamma. **CLOB read endpoints are also free and unauthenticated** — we should be using them too.

## Rate limits (very generous)

| Endpoint | Limit |
|---|---|
| Gamma `/events` | 500 req / 10s |
| Gamma `/markets` | 300 req / 10s |
| CLOB `/book` | 1,500 req / 10s |
| CLOB `/price` | 1,500 req / 10s |
| CLOB `/midpoint` | 1,500 req / 10s |
| CLOB `/prices-history` | 1,000 req / 10s |
| Data API general | 1,000 req / 10s |

Cloudflare-throttled, not rejected — exceeding the limit means delays not errors. Our current 60s polling is well within bounds; we can poll **every 1-2 seconds** for live price tracking if desired.

## Concrete upgrades to existing scripts

### 1. Use CLOB order book instead of `outcomePrices`

We currently read `outcomePrices` from Gamma — these are last-trade prices, can be stale. The CLOB endpoint gives **real bids/asks**:

```python
import requests
def get_book(token_id: str) -> dict:
    r = requests.get(f"https://clob.polymarket.com/book?token_id={token_id}")
    return r.json()
# Returns: {"asset_id": ..., "bids": [{"price":"0.475","size":"1234"},...],
#           "asks": [{"price":"0.485","size":"567"},...]}
```

**Why it matters**: a market showing "47.5%" via Gamma `outcomePrices` might actually have:
- Last trade: 47.5¢
- Best bid: 45¢ (where you can sell)
- Best ask: 50¢ (where you can buy)
- Spread: 5¢ (10% of price — illiquid, edge gets eaten)

Without CLOB book, we can't tell the difference between a tight 1¢-spread market (real price) and a 10¢-spread market (no real fair value). **Our current `wti_validator.py` and `crypto_validator.py` are reading midpoint when they should be reading the side they'd execute on.**

### 2. Use spread to filter illiquid markets

```python
def get_spread(token_id: str) -> float:
    r = requests.get(f"https://clob.polymarket.com/spread?token_id={token_id}")
    return float(r.json()["spread"])
```

Add to validators: skip / flag any flagged "edge" where spread > 5¢. A 13pp model edge is meaningless if the spread is 8pp.

### 3. Use `prices-history` for backtesting

```python
def get_history(token_id: str, interval: str = "1h", fidelity: int = 60) -> list:
    """Returns [{"t": unix_ts, "p": price}, ...]"""
    r = requests.get(f"https://clob.polymarket.com/prices-history",
                     params={"market": token_id, "interval": interval, "fidelity": fidelity})
    return r.json()["history"]
```

Could backtest our model edge calls — did "BTC reach $85k YES @ 47.5%" prices actually move in our favour after we flagged them?

### 4. Use Data API `open-interest` for sizing

```python
def get_open_interest(market_id: str) -> dict:
    r = requests.get(f"https://data-api.polymarket.com/oi?market={market_id}")
    return r.json()
```

Helps decide max position size relative to total open interest. Edge with $1M OI is sizable; same edge with $50k OI is theoretical only.

### 5. Use Gamma `search` instead of guessing slugs

We currently hardcode slugs and break when Polymarket renames events. Better:

```python
def search_polymarket(query: str) -> dict:
    r = requests.get(f"https://gamma-api.polymarket.com/public-search",
                     params={"q": query, "limit_per_type": 10})
    return r.json()
```

Returns matched events, markets, profiles. Our crypto_validator.py spent effort searching for the right BTC market slug — `search_polymarket("bitcoin may 2026")` would have found it instantly.

## The negRisk discovery — explains the GOP nominee finding

The Polymarket docs reveal the **negRisk** mechanism, which directly explains the false positive my cross_venue_scanner.py flagged earlier.

A negative-risk event is a multi-outcome event where **only one outcome can win**. The Gamma API marks these with `"negRisk": true`. Examples: presidential nominees, Best Picture, Champions League winner.

Two important properties:

1. **Sum of YES prices < 100% normally** — the missing probability is implicit "Other" (some outcome not on the listed ladder). When the GOP nominee event sums to 59%, it means the market thinks 41% chance the nominee is someone unlisted (e.g. Tucker Carlson, Greg Abbott, an outsider).

2. **Capital-efficient conversion**: 1 NO share converts to 1 YES across all OTHER markets in the same event. This means buying NO on Vance is functionally equivalent to buying a basket of YES on every other candidate.

Augmented negRisk markets (`"negRiskAugmented": true`) additionally have placeholder slots for new candidates that emerge mid-cycle, plus an explicit "Other" outcome.

**Implication for our scanner**: the cross_venue_scanner needs to detect `negRisk: true` events and:
- Not flag a YES-sum < 100% as inconsistency
- Compute "implicit Other" probability = 100% − sum(YES)
- Flag if implicit Other > 50% (could indicate the listed slate is missing the actual frontrunner)
- Flag if implicit Other < 0% (real overround arbitrage — sum > 100%)

Quick fix to add to `cross_venue_scanner.py`:

```python
event = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}").json()[0]
neg_risk = event.get("negRisk", False)
yes_sum = sum(get_yes_price(m) for m in event["markets"])
if neg_risk:
    implicit_other = 1.0 - yes_sum
    print(f"negRisk event: sum {yes_sum:.1%}, implicit Other {implicit_other:.1%}")
else:
    if abs(yes_sum - 1.0) > 0.05:
        print(f"Inconsistency: sum {yes_sum:.1%} (no negRisk, expected 100%)")
```

## Recommendation for the project

Three priorities:

1. **Migrate validators to CLOB book endpoints** for realistic edge calculation. Replace `outcomePrices` reads with `/book` reads, compute edge against the *executable* price (best ask for BUY, best bid for SELL), not the midpoint.

2. **Fix cross_venue_scanner.py** to handle negRisk correctly and use Gamma `search` instead of hardcoded slugs.

3. **Add open interest + spread filters** to `OPPORTUNITIES.md` — a 13pp edge with $50k OI and 5¢ spread is not the same as 13pp edge with $5M OI and 1¢ spread.

The core infrastructure is right. The upgrades are about going from "fair value comparison" to "actual executable arbitrage estimation".

## Other Gamma/Data API features worth knowing

- **`/sports`** endpoints return team metadata for the NBA / NFL / etc markets — useful if we extend `nba_validator.py` to do automatic team-name matching against Bovada
- **`/tags/list-tags`** returns the complete tag taxonomy — we can browse all crypto markets, all politics markets, all sports markets without knowing slugs
- **`/series`** groups markets that recur (e.g. "Will Trump tweet on day X" series) — gives historical context for our model calibration
- **Geoblock endpoint** confirms which countries can trade on which markets — important caveat if user is GB-based (Polymarket geoblocked in UK, can read but not trade)
