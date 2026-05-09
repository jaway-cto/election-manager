# Setup checklist — `odds`

Steps in dependency order. Each "Verify" line tells you how to confirm it worked.

After every step, run:
```powershell
cd C:\Dev\odds\scripts
python verify_setup.py
```
This prints a coloured pass/fail of every system component.

---

## Step 1 — Python dependencies

```powershell
pip install requests scipy numpy openpyxl python-telegram-bot
```

Optional (Windows ARM64 may fail to compile — this is fine, the system works without):
```powershell
pip install pdfminer.six web3 py-clob-client
```

The pdfminer dep is for SCOTUS PDF parsing (best-effort; the SCOTUS poller still alerts on case title even without it). The web3/py-clob-client deps are only needed for **automated** Polymarket trading. You can do paper-trading and manual trading without them.

**Verify**:
```powershell
python verify_setup.py
```
Look for `[ OK ]  requests / scipy / numpy / openpyxl` in the deps section.

---

## Step 2 — Telegram bot (5 min, free, recommended)

1. Open Telegram, search **@BotFather**, click Start
2. Send `/newbot`, follow prompts (any name works)
3. BotFather replies with `1234567890:ABCdefGhIJkLmNoPQRsT…` — **save the whole token**
4. Click the link to your bot, send any message ("hi")
5. Get your chat ID: open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   - Look for `"chat":{"id":123456789` — that number is your chat ID
6. Set the env vars (PowerShell):
   ```powershell
   setx TELEGRAM_BOT_TOKEN "1234567890:ABCdefGhIJkLmNoPQRsT"
   setx TELEGRAM_CHAT_ID "123456789"
   ```
7. **Open a NEW PowerShell window** (env vars only apply to new shells)

**Verify**:
```powershell
cd C:\Dev\odds\scripts
python notify.py "test from odds"
```
You should see a message arrive on Telegram within a second.

---

## Step 3 — The Odds API (free tier, instant, optional)

Required only for the sports validator. Skip if you don't care about NBA/NFL/EPL/UFC etc. cross-book arbs.

1. Go to <https://the-odds-api.com>
2. Click "Get a free API key", register email
3. Email arrives with key like `abc123def456…`
4. PowerShell:
   ```powershell
   setx THE_ODDS_API_KEY "abc123def456..."
   ```
5. New PowerShell window

**Verify**:
```powershell
python sports_validator.py --list-sports | head -10
```
Lists ~30 sports.

---

## Step 4 — Polymarket trade credentials (30-60 min, optional)

**Skip this entirely until your 30-day paper trial is complete**. The system flags edges and you trade manually via Polymarket UI for now. After 30 days of capture, run `python backtest_validator.py decide` — it tells you which scanners earned the right to auto-trade.

When you're ready:

1. Install MetaMask (browser extension)
2. **Create a NEW wallet specifically for this bot** — never use your main wallet
3. Export the seed phrase, save to a password manager
4. Visit <https://polymarket.com>, click Log In, connect MetaMask
5. Polymarket creates a "proxy wallet". Get its address from the Polymarket UI (Profile → Wallets → "Funder" address)
6. Fund the proxy wallet:
   - **UK route**: Coinbase UK → buy USDC → withdraw to **Polymarket deposit address (Polygon network)**. Round-trip cost ~1.5%.
7. In Polymarket UI: Profile → API Keys → Create new key. Save:
   - `apiKey`
   - `secret`
   - `passphrase`
8. Export your bot wallet's private key from MetaMask (Account Details → Show private key)
9. PowerShell:
   ```powershell
   setx PM_PRIVATE_KEY "0x<your-bot-wallet-private-key>"
   setx PM_API_KEY "<from-polymarket-ui>"
   setx PM_API_SECRET "<from-polymarket-ui>"
   setx PM_API_PASSPHRASE "<from-polymarket-ui>"
   setx PM_PROXY_ADDRESS "0x<your-polymarket-proxy-address>"
   ```
10. **Do NOT yet** set `PM_TRADING_ENABLED=1`. This stays unset during paper trial.

**Verify**:
```powershell
python clob_client.py
```
Should print `creds ready: True`.

---

## Step 5 — Betfair Exchange (~3 day approval, optional)

Skip if you don't have a verified, funded Betfair UK account. You can do everything else without this.

1. Have a Betfair UK account with deposit (gambling-exempt for UK individuals)
2. Go to <https://developer.betfair.com>, register, request live app key
3. Wait 1-3 business days for approval email
4. PowerShell:
   ```powershell
   setx BETFAIR_APP_KEY "<approved-key>"
   setx BETFAIR_USERNAME "<your-betfair-login>"
   setx BETFAIR_PASSWORD "<your-betfair-password>"
   ```

**Verify**:
```powershell
python -m venues.betfair_client
```
Should print event types (Soccer / Tennis / Cricket etc.).

After this, **Send me** confirmation and I'll fill in `data/pm_betfair_pairs.csv` with real Betfair market IDs for the top 20 cross-venue pairs.

---

## Step 6 — UK tax provision (do BEFORE first profitable month)

Not optional if profits exceed £1k.

1. Email a **chartered accountant** who handles UK crypto/spread-betting clients
2. Ask: "Will my Polymarket activity be classified as gambling (tax-free), CGT (24% above £3k allowance), or trading income (income tax)?"
3. Cost: £400-800 for a written opinion
4. Until you have the opinion: **set aside 35% of every realised PnL** in a separate account

The red-team analysis (`docs/RED_TEAM_FINDINGS.md`) flagged this as expected loss **30-40% of profits** if HMRC reclassifies after 12 months of consistent algorithmic profit. Worth £500.

---

## Step 7 — Run it

Daily run:

```powershell
cd C:\Dev\odds\scripts

# Single dashboard scan, writes docs/DASHBOARD.md
python unified_arb_dashboard.py

# Watch mode (refreshes every 5 min)
python unified_arb_dashboard.py --watch 300
```

Background daemon (separate window):

```powershell
# Polls SCOTUS, NHC, FDA — alerts to Telegram on news
python -m pollers.daemon
```

Position tracking:

```powershell
# When you manually place a trade on Polymarket UI, log it:
python positions.py open --market-id <id> --token-id <token> --side BUY --size 100 --px 0.95 --validator tail-decay --label "Trump China"

# Live mark-to-market
python positions.py mtm

# PnL by validator
python positions.py pnl
```

After 30 days:

```powershell
# THE decision: which scanners deserve live capital?
python backtest_validator.py decide --hold-days 7
```

The `decide` table has 4 verdicts: ENABLE LIVE / PAPER ONLY / DROP / MORE DATA. Only enable `PM_TRADING_ENABLED=1` for a scanner once its row says ENABLE LIVE.

---

## Killswitch

At any moment, halt all trading:

```powershell
python killswitch.py trip "I want to think"
```

Re-arm:

```powershell
python killswitch.py reset
```

Or set the env var `ODDS_TRADING_HALT=1` (checked every loop iteration).

---

## Minimum to start (Steps 1, 2, 3, 7)

About 15 minutes of work. You'll have:
- Live edge dashboard
- Telegram alerts on new signals
- Sports validator (if Step 3 done)
- 24/7 SCOTUS/NHC/FDA pollers
- Backtest harness auto-capturing signals for 30-day evaluation

No funds at risk yet. Steps 4-6 unlock auto-trading and Betfair when you're ready.
