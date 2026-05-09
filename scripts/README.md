# Scripts directory

## Core framework
- `validator_core.py` — CLOB book/spread/OI fetching, EdgeRow schema, edge classification, filter gates, attempt-score ranking
- `clob_client.py` — Polymarket client wrapper. Read-only by default; trade-enabled when `PM_TRADING_ENABLED=1` + creds set
- `notify.py` — Telegram alert sink (`alert()`, `fyi()`, `event()`); falls back to stdout/log file
- `killswitch.py` — Halt mechanism (env var `ODDS_TRADING_HALT=1` OR file `C:\Dev\odds\HALT`)
- `positions.py` — SQLite position book + fills + heartbeats. CLI: `list`, `mtm`, `pnl`, `open`, `close`

## Information-edge pollers (Week 3)
- `pollers/scotus.py` — Slip opinion drops on supreme court term page
- `pollers/nhc.py` — Hurricane advisories from `CurrentStorms.json`
- `pollers/fda.py` — FDA press-release RSS, auto-linked to PM FDA markets
- `pollers/daemon.py` — Async orchestrator running all pollers on independent cadences. State persisted in `C:\Dev\odds\data\pollers.sqlite`.

```powershell
# One-shot all pollers
python -m pollers.daemon --once

# Daemon mode (until killed)
python -m pollers.daemon
```

## Cross-venue / models (Week 4)
- `venues/betfair_client.py` — Betfair Exchange Betting API wrapper. Reads `BETFAIR_APP_KEY`/`BETFAIR_USERNAME`/`BETFAIR_PASSWORD` env vars; degrades gracefully without keys.
- `cross_venue_pm_betfair.py` — Scans pairs in `data/pm_betfair_pairs.csv`, computes both arb directions (BUY PM + LAY BF, SELL PM + BACK BF) net of taker fees and 5% Betfair commission.
- `data/pm_betfair_pairs.csv` — Manual mapping of PM slug × Betfair market id. Fill in `bf_market_id` column once Betfair app key is approved (~3 day approval at developer.betfair.com).
- `models/oscar_precursors.py` — Logistic regression predicting Oscar Best Picture from BAFTA/PGA/DGA/SAG/CCA/Globe winners. 25-year training set, 68% LOYO accuracy. PGA + CCA strongest positive predictors; BAFTA negative (UK-bias).

## Multi-event scanners
- `tail_decay_scanner.py` — Residual asks on past/near-deadline markets, with auto-execution gates
- `lp_rewards_scanner.py` — CLOB v2 rewards-eligible markets ranked for solo MM
- `negrisk_scanner.py` — Sum YES across negRisk events, flag basket arbs
- `mm_simulator.py` — Paper-mode market maker for a single token

## Validators (CLOB-aware)
- `crypto_validator.py` — BTC threshold markets vs Deribit IV
- `wti_validator.py` — WTI barrier touch vs OVX
- `eurovision_validator.py` — PM vs aggregator
- `french_pres_validator.py` — PM vs Smarkets
- `nba_validator.py` — Bovada devig vs PM CLOB
- `macro_validator.py` — CME FedWatch vs PM Fed-decision (CME 403's solo callers — degrades gracefully)
- `sports_validator.py` — The Odds API multi-book devig vs PM moneylines (needs `THE_ODDS_API_KEY`)
- `tail_decay_scanner.py` — Hunt residual asks 0.92-0.995 on near/past-deadline markets

## Orchestration
- `unified_arb_dashboard.py` — Run all validators in parallel, rank by attempt score, write `docs/DASHBOARD.md`
- `backtest_validator.py` — Replay signals against CLOB prices-history

## Trading workflow (paper → live)

1. **Read-only** (current default): `python unified_arb_dashboard.py`
2. **Configure trading**: set `PM_PRIVATE_KEY`, `PM_API_KEY`, `PM_API_SECRET`, `PM_API_PASSPHRASE`, `PM_PROXY_ADDRESS`, then `PM_TRADING_ENABLED=1`
3. **Halt** (any time): `python killswitch.py trip "reason"` OR `setx ODDS_TRADING_HALT 1`
4. **Re-arm**: `python killswitch.py reset` AND unset env var
5. **Logs**: `C:\Dev\odds\logs\notify.log`
6. **Position book**: `C:\Dev\odds\data\positions.sqlite`

## Telegram setup (one-time)

1. Talk to @BotFather, `/newbot`, save token
2. Send your bot any message, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat_id
3. `setx TELEGRAM_BOT_TOKEN "1234:ABC..."`
4. `setx TELEGRAM_CHAT_ID "123456789"`
5. Test: `python notify.py "hello from odds"`

## Manual position open/close (during paper-trading phase)

```powershell
# When the scanner alerts on a tail-decay candidate and you place a trade by hand on Polymarket:
python positions.py open --market-id 0xabc --token-id 0x123 --side BUY --size 100 --px 0.95 --validator tail-decay --label "Trump visits China"

# When it resolves:
python positions.py close --id 1 --px 1.00

# Check current state:
python positions.py list
python positions.py mtm
python positions.py pnl
```
