# Polymarket Trading Bot - Project Documentation

## Server Details

| Item | Value |
|------|-------|
| Server IP | `174.138.5.183` |
| User | `root` |
| Bot Path | `~/polymarket_bot/` |
| Log Path | `~/polybot/bot.log` |
| Credentials | `~/.env` (NOT in project folder) |

### SSH Access
```bash
ssh root@174.138.5.183
```

---

## Versioning

**IMPORTANT**: Always update the version when making changes to the bot.

### Version Location
The bot version is defined at the top of `trading_bot_smart.py`:
```python
BOT_VERSION = {
    "version": "v1.2",
    "codename": "Silent Thunder",
    "date": "2026-01-16",
    "changes": "Fix: PAIRING_MODE race condition causing duplicate orders"
}
```

### How to Update Version
1. Increment the version number (v1.2 → v1.3)
2. Pick a new codename (two words: `[Adjective] [Animal/Object]`)
3. Update the date
4. Describe the changes
5. Update `BOT_REGISTRY.md` with the new version

### Verify Running Version
On server:
```bash
head -30 ~/polymarket_bot/trading_bot_smart.py | grep -A4 "BOT_VERSION"
```

Or check the log startup message:
```bash
grep "POLYBOT" ~/polybot/bot.log | tail -1
```

### Deploy New Version

**IMPORTANT**: Always SSH in interactively to start the bot. Long one-liner commands via `ssh "command"` break due to line wrapping issues.

```bash
# Step 1: From Mac - push files to server
scp ~/MarkWatney/trading_bot_smart.py ~/MarkWatney/sheets_dashboard.py root@174.138.5.183:~/polymarket_bot/

# Step 2: SSH into server
ssh root@174.138.5.183

# Step 3: On server - stop old bot
pkill -f trading_bot_smart.py

# Step 4: On server - start new bot (run as ONE command)
cd ~/polymarket_bot && export GOOGLE_SHEETS_SPREADSHEET_ID=1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74 && export GOOGLE_SHEETS_CREDENTIALS_FILE=~/.google_sheets_credentials.json && nohup python3 trading_bot_smart.py > /dev/null 2>&1 &

# Step 5: Verify it's running
tail -20 ~/polybot/bot.log
```

### Version History
See `BOT_REGISTRY.md` for full version history with codenames and changes.

### Git Version Control
**IMPORTANT**: Each version is committed to GitHub individually so we can rollback if issues arise.

```bash
# Commit new version
git add trading_bot_smart.py BOT_REGISTRY.md
git commit -m "v1.X: Brief description of changes"
git push

# Rollback to previous version
git checkout HEAD~1 -- trading_bot_smart.py
scp ~/MarkWatney/trading_bot_smart.py root@174.138.5.183:~/polymarket_bot/

# Rollback to specific version (find commit hash first)
git log --oneline trading_bot_smart.py
git checkout <commit-hash> -- trading_bot_smart.py
```

---

## File Structure

### Core Bot Files (Project Root)

| File | Description |
|------|-------------|
| `trading_bot_smart.py` | **MAIN BOT** - BTC 15-minute Up/Down arbitrage + 99c capture strategy |
| `sheets_logger.py` | **Google Sheets logging** - Logs events and per-second ticks to Google Drive |
| `chainlink_feed.py` | Fetches BTC price from Chainlink oracle (same source as Polymarket settlement) |
| `orderbook_analyzer.py` | Analyzes order book imbalance to detect buy/sell pressure |
| `auto_redeem.py` | Monitors and notifies about claimable winning positions |
| `imbalance_tracker.py` | Tracks order book imbalance correlation with price movements |
| `.env` | Local credentials (copy to `~/.env` on server) |

### Data Files

| File | Description |
|------|-------------|
| `imbalance_data.json` | Historical order book imbalance data |
| `imbalance_summary.txt` | Summary of imbalance signal accuracy |
| `tracker_log.txt` | Imbalance tracker output log |
| `server_bot.log` | Copy of server bot log for local analysis |
| `trades_smart.json` | Trade history (created by bot) |

### Other Directories

| Directory | Description |
|-----------|-------------|
| `polymarket_arb_bot/` | Older bot version (reference only) |
| `__pycache__/` | Python bytecode cache |

---

## How The Bot Works

### Architecture Overview

```
trading_bot_smart.py
    ├── CLOB Client (py-clob-client) - Places orders on Polymarket
    ├── sheets_logger.py - Logs to Google Sheets (events + per-second ticks)
    ├── chainlink_feed.py - Gets authoritative BTC price
    ├── orderbook_analyzer.py - Detects order book imbalance signals
    └── auto_redeem.py - Monitors winning positions for redemption
```

### Trading Strategies

#### 1. ARB Trading (Main Strategy)
- Trades BTC 15-minute Up/Down prediction markets
- Buys BOTH sides when combined cost < $1.00 (guaranteed profit)
- Example: Buy UP @ 39c + DOWN @ 60c = 99c cost, wins $1.00 = 1c profit

**Entry Conditions:**
- Cheap side <= 42c (`DIVERGENCE_THRESHOLD`)
- Expensive side >= 58c (`MIN_EXPENSIVE_SIDE_PRICE`)
- Time remaining > 5 minutes (`MIN_TIME_FOR_ENTRY`)
- No existing position imbalance

#### 2. 99c Capture (Supplemental Strategy)
- Single-side bet on near-certain winners
- Places 99c bid when one side has very high confidence
- **Confidence Formula:** `confidence = ask_price - time_penalty`

**Time Penalties:**
| Time Remaining | Penalty |
|----------------|---------|
| < 60 seconds   | 0%      |
| 60-120 seconds | 3%      |
| 2-5 minutes    | 8%      |
| > 5 minutes    | 15%     |

**Trigger:** Confidence >= 95% (`CAPTURE_99C_MIN_CONFIDENCE`)

### State Machine

```
QUOTING → PAIRING_MODE → DONE
    ↑___________|
```

1. **QUOTING**: Looking for arb opportunities
2. **PAIRING_MODE**: One side filled, must complete the other side
3. **DONE**: Position balanced, waiting for window close

### Risk Management

| Guard | Value | Description |
|-------|-------|-------------|
| `FAILSAFE_MAX_BUY_PRICE` | 85c | Never buy above 85c (except 99c capture) |
| `FAILSAFE_MIN_BUY_PRICE` | 5c | Never buy garbage prices |
| `FAILSAFE_MAX_SHARES` | 50 | Max shares per order |
| `FAILSAFE_MAX_ORDER_COST` | $10 | Max order cost |
| `MICRO_IMBALANCE_TOLERANCE` | 0.5 | Accept 0.5 share difference as "balanced" |
| `BAIL_TIME_REMAINING` | 90s | Force exit at 90s if imbalanced |
| `HARD_FLATTEN_SECONDS` | 10s | Emergency sell at 10s before close |

### Understanding Bot Status Messages

The bot displays status every second in this format:
```
[HH:MM:SS] STATUS | T-XXXs | UP:XXc DN:XXc | pos:X/X | reason
```

**Status Values:**
| Status | Meaning |
|--------|---------|
| `IDLE` | No position, waiting for divergence opportunity |
| `PAIRING` | One leg filled, actively trying to complete the other side |
| `PAIRED` | Both legs filled, waiting for window to close |
| `IMBAL` | Position imbalance detected, in emergency mode |

**IDLE Reasons (why the bot isn't trading):**
| Reason | Meaning | What To Do |
|--------|---------|------------|
| `no diverge (XXc>42c)` | Cheap side price is above 42c threshold | **Normal** - wait for prices to diverge |
| `weak (XXc<58c)` | Expensive side below 58c (near 50/50 market) | **Normal** - need clearer trend |
| `too late` | Less than 90s remaining in window | **Normal** - won't enter so close to expiry |
| `late entry (XXXs<300s)` | Less than 5 minutes remaining | **Normal** - won't enter with <5 min left |
| `checking...` | Divergence detected, attempting to trade | Trade in progress |

**Important:** The bot staying IDLE with `no diverge` is **normal behavior** when the market hasn't diverged enough. The bot is designed to wait for clear opportunities (one side ≤42c, other ≥58c).

### Troubleshooting "Bot Is Always Idle"

1. **Check the prices in the log:**
   ```
   [19:00:00] IDLE | T-899s | UP:54c DN:48c | pos:0/0 | no diverge (48c>42c)
   ```
   - If cheap side (48c) is above 42c, the bot is correctly waiting
   - The bot will trade when cheap side drops to 42c or below

2. **Verify divergence threshold:**
   - For a trade: need cheap side ≤42c AND expensive side ≥58c
   - Example that WILL trade: `UP:60c DN:41c` (41c ≤ 42c, 60c ≥ 58c)
   - Example that WON'T: `UP:55c DN:46c` (46c > 42c)

3. **Check if bot is running:**
   ```bash
   ssh root@174.138.5.183 "ps aux | grep trading_bot"
   ```

4. **Watch live logs:**
   ```bash
   ssh root@174.138.5.183 "tail -f ~/polybot/bot.log"
   ```

5. **Look for `STRONG_DIVERGENCE` messages:**
   - When you see this, the bot detected an opportunity and is attempting to trade
   - If you never see this, the market simply hasn't diverged enough

---

## Configuration

### Credentials Location
**IMPORTANT:** Bot loads credentials from `~/.env`, NOT from project folder.

```bash
# On server, credentials must be at:
~/.env

# Contains:
PRIVATE_KEY=<your_private_key>
WALLET_ADDRESS=<your_wallet_address>
```

### Key Constants (trading_bot_smart.py)

```python
# 99c Capture Settings (lines 232-246)
CAPTURE_99C_ENABLED = True
CAPTURE_99C_MAX_SPEND = 5.00
CAPTURE_99C_BID_PRICE = 0.99
CAPTURE_99C_MIN_TIME = 10
CAPTURE_99C_MIN_CONFIDENCE = 0.95  # 95% confidence threshold

# Divergence Settings (lines 160-161)
DIVERGENCE_THRESHOLD = 0.42       # Cheap side must be <= 42c
MIN_EXPENSIVE_SIDE_PRICE = 0.58   # Expensive side must be >= 58c

# Timing (lines 128-131)
PAIR_DEADLINE_SECONDS = 90
TAKER_AT_SECONDS = 20
HARD_FLATTEN_SECONDS = 10
```

---

## Commands Reference

### Start Bot on Server
**Best method**: SSH in interactively, then run:
```bash
ssh root@174.138.5.183
cd ~/polymarket_bot && export GOOGLE_SHEETS_SPREADSHEET_ID=1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74 && export GOOGLE_SHEETS_CREDENTIALS_FILE=~/.google_sheets_credentials.json && nohup python3 trading_bot_smart.py > /dev/null 2>&1 &
```

### Stop Bot on Server
```bash
ssh root@174.138.5.183 "pkill -f trading_bot_smart.py"
```

### Check if Bot Running
```bash
ssh root@174.138.5.183 "ps aux | grep trading_bot"
```

### View Live Logs
```bash
ssh root@174.138.5.183 "tail -f ~/polybot/bot.log"
```

### View Recent Logs
```bash
ssh root@174.138.5.183 "tail -100 ~/polybot/bot.log"
```

### Upload Updated Bot
```bash
scp trading_bot_smart.py root@174.138.5.183:~/polymarket_bot/
```

### Full Restart Sequence
```bash
# 1. Upload new code (from Mac)
scp ~/MarkWatney/trading_bot_smart.py ~/MarkWatney/sheets_dashboard.py root@174.138.5.183:~/polymarket_bot/

# 2. SSH into server
ssh root@174.138.5.183

# 3. On server - kill old bot
pkill -f trading_bot_smart.py

# 4. On server - start new bot (run as ONE command)
cd ~/polymarket_bot && export GOOGLE_SHEETS_SPREADSHEET_ID=1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74 && export GOOGLE_SHEETS_CREDENTIALS_FILE=~/.google_sheets_credentials.json && nohup python3 trading_bot_smart.py > /dev/null 2>&1 &

# 5. Verify running
tail -20 ~/polybot/bot.log
```

---

## Current State (as of 2026-01-15)

### Recent Changes Made

1. **Confidence-Based 99c Capture**
   - Replaced tiered thresholds with confidence formula
   - `confidence = ask_price - time_penalty`
   - Time penalties: <60s=0%, 60-120s=3%, 2-5min=8%, 5+min=15%

2. **99c Capture Fill Notification**
   - Added `capture_99c_fill_notified` flag to window_state
   - Bot now shows notification when 99c capture order fills

3. **Pairing Mode Bug Fix**
   - Added `get_arb_imbalance()` function
   - Excludes 99c capture shares from pairing calculations
   - Prevents bot from trying to "pair" 99c capture positions

4. **Micro Imbalance Tolerance**
   - Increased from 0.1 to 0.5
   - Fixes bug where 4.9 vs 5.0 shares triggered rebalancing

5. **Failsafe Bypass for 99c**
   - Added `bypass_price_failsafe` parameter to `place_limit_order()`
   - Allows 99c capture to bypass 85c max price limit

### Known Issues

1. **99c Capture at 95% Can Lose**
   - A 95% confidence 99c capture trade lost when market reversed in final 60 seconds
   - Consider raising `CAPTURE_99C_MIN_CONFIDENCE` to 0.98 for more conservative trading
   - To hit 98%: Need 98c+ ask price AND <60 seconds remaining

2. **Order Book Imbalance Signals**
   - Current accuracy: 0% (50/50 wrong signals in testing)
   - Signals are logged but NOT used for trading decisions
   - `USE_ORDERBOOK_SIGNALS = True` but only for logging

### Bot Status
- Bot is currently **RUNNING** on server
- Google Sheets logging: **ENABLED** (per-second ticks + events)

---

## RPC Configuration (Polygon)

**IMPORTANT:** Use Alchemy (or Infura) RPC instead of public `polygon-rpc.com` which gets rate limited.

### Setup Alchemy (Free)
1. Sign up at https://www.alchemy.com
2. Create app: Polygon Mainnet
3. Copy HTTPS URL: `https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY`
4. Add to server `~/.env`:
   ```bash
   POLYGON_RPC=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY
   ```

### Gas for Redemptions
- EOA wallet needs MATIC for gas (~0.002-0.005 per redeem)
- Proxy wallet has MATIC but can't spend it directly on gas
- Use `send_matic.py` to transfer MATIC from Proxy to EOA:
  ```bash
  python3 send_matic.py  # Sends 0.5 MATIC to EOA
  ```

### Check Balances
```bash
cd ~/polymarket_bot && python3 auto_redeem.py --test
```

---

## Dependencies (Server)

```bash
pip3 install python-dotenv --break-system-packages
pip3 install py-clob-client --break-system-packages
pip3 install gspread google-auth --break-system-packages
```

Required packages:
- `py-clob-client` - Polymarket CLOB trading API
- `python-dotenv` - Environment variable loading
- `requests` - HTTP requests
- `eth-account` - Ethereum account handling
- `gspread` - Google Sheets API client
- `google-auth` - Google authentication

---

## Wallet Information

| Item | Value |
|------|-------|
| Local Wallet | `0x636796704404959f5Ae9BEfEb2B3880eadf6960a` |
| Private Key | In `~/.env` (never commit!) |

---

## API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `https://clob.polymarket.com` | CLOB API for trading |
| `https://gamma-api.polymarket.com` | Market data |
| `https://data-api.polymarket.com` | Position data |
| `https://api.coinbase.com` | BTC price (fallback) |
| Chainlink RPC | BTC price (primary, via chainlink_feed.py) |

---

## Telegram Notifications

Bot sends Telegram notifications for:
- PROFIT_PAIR - Successful arbitrage with guaranteed profit
- LOSS_AVOID_PAIR - Completed at loss but risk eliminated
- HARD_FLATTEN - Emergency position close

Config file: `~/.telegram-bot.json`
```json
{
  "token": "YOUR_BOT_TOKEN",
  "chat_id": "YOUR_CHAT_ID"
}
```

---

## Google Sheets Logging

All trading activity is logged to Google Sheets for analysis.

### Spreadsheet
- **ID:** `1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74`
- **Name:** "Polymarket Bot Log"

### Sheets/Tabs

| Sheet | What's Logged | Frequency |
|-------|---------------|-----------|
| **Ticks** | Per-second price data (TTL, UP/DN ask, positions, BTC price, order book imbalance) | Every second (batched every 30s) |
| **Events** | Key events (ARB_ORDER, ARB_FILL, CAPTURE_99C, PROFIT_PAIR, LOSS_AVOID, HARD_FLATTEN, ERROR) | When they occur |
| **Windows** | Window summaries (positions, outcome, PnL, 99c capture info) | At window end |

### Server Environment Variables
```bash
# These must be exported before starting the bot:
export GOOGLE_SHEETS_SPREADSHEET_ID=1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74
export GOOGLE_SHEETS_CREDENTIALS_FILE=~/.google_sheets_credentials.json
```

To make permanent, add to `~/.bashrc`.

### Credentials
- **File:** `~/.google_sheets_credentials.json` (service account JSON key)
- **Service Account:** `polybot-sheets@polymarket-bot-logging.iam.gserviceaccount.com`
- **Google Cloud Project:** `polymarket-bot-logging`

### Start Bot with Sheets Logging
```bash
export GOOGLE_SHEETS_SPREADSHEET_ID=1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74
export GOOGLE_SHEETS_CREDENTIALS_FILE=~/.google_sheets_credentials.json
cd ~/polymarket_bot && nohup python3 trading_bot_smart.py > /dev/null 2>&1 &
```

### Verify Sheets Logging
```bash
tail -100 ~/polybot/bot.log | grep -i sheets
# Should see: [SHEETS] Connected to Google Sheets
# And periodically: [SHEETS] Flushed X ticks
```

---

## Session Log

### 2026-01-24
- **Deployed v1.19 "Laser Falcon"** - ARB disabled, 99c sniper only mode
  - Added `ARB_ENABLED = False` flag to disable arbitrage trading
  - 99c capture strategy remains fully active
  - Goal: Perfect the 99c strategy before re-enabling ARB
- **Fixed auto_redeem.py rate limiting:**
  - Problem: Public `polygon-rpc.com` was rate limiting redemptions
  - Solution: Added Alchemy RPC (`POLYGON_RPC` in ~/.env)
  - Added retry logic with exponential backoff for RPC calls
- **Added send_matic.py utility:**
  - Sends MATIC from Proxy (Safe) wallet to EOA for gas
  - EOA needs MATIC to execute redemption transactions
  - Funded EOA with 0.5 MATIC
- **SSH access improvements:**
  - Fixed slow SSH by disabling DNS lookup (`UseDNS no` in sshd_config)
  - Added SSH key for Claude Code access

### 2026-01-15
- Created CLAUDE.md documentation file
- **Added Google Sheets logging:**
  - Created `sheets_logger.py` module
  - Set up Google Cloud service account (`polymarket-bot-logging` project)
  - Logs key events (orders, fills, pairs, errors) to "Events" sheet
  - Logs per-second tick data (prices, positions, BTC, OB imbalance) to "Ticks" sheet
  - Logs window summaries to "Windows" sheet
  - Ticks buffered locally, flushed every 30 seconds
  - Spreadsheet ID: `1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74`
- Bot restarted with Sheets logging enabled
- **Added "Understanding Bot Status" troubleshooting section:**
  - Explains status values (IDLE, PAIRING, PAIRED, IMBAL)
  - Documents IDLE reasons (`no diverge`, `weak`, `too late`, etc.)
  - Added troubleshooting steps for "bot is always idle"
  - Key insight: IDLE with `no diverge` is NORMAL when market hasn't diverged (cheap side >42c)
- **Fixed critical `slug` undefined bug:**
  - Bug: `NameError: name 'slug' is not defined` in `check_and_place_arb()`
  - Cause: `sheets_log_event()` calls used `slug` (only defined in main()) instead of `window_state.get('window_id', '')`
  - Impact: Error occurred AFTER order placed but BEFORE `arb_placed_this_window = True`, causing duplicate orders
  - Fixed 3 locations: lines 1209, 1394, 1408
- **Fixed Google Sheets logging silent failure:**
  - Bug: Sheets stopped updating after row 22 (API connection died silently)
  - Cause: No retry logic, no reconnection when API fails
  - Fix: Added retry with exponential backoff to `log_event()`, `log_window()`, `flush_ticks()`
  - Now retries 3x and forces reconnection on failure
- **99c Capture UI improvement:**
  - Changed `✅ Order placed!` to `🔭 Order placed, watching for fill...`
  - Celebration now only shows when order actually fills
- **Added mid-window startup skip:**
  - If bot starts with < 14 min remaining, it waits for fresh window
  - Shows `WAIT` status with "waiting for fresh window" reason
  - Prevents trading on incomplete window data
- **CRITICAL FIX: Position tracking now protected from stale API:**
  - Bug: API returned 0/0 (stale cache), bot overwrote its own position tracking
  - Result: Bot forgot about pending orders, went IDLE with pos:0/0
  - Fix: Position tracking can ONLY INCREASE, never decrease
  - Validation: If local shows fills but API says 0/0, ignore API
  - Applied to: AGGRESSIVE_COMPLETION, main loop sync, run_pairing_mode
  - Uses `max(local, api)` to ensure fills are never lost
- **BULLETPROOF: Added dual-source verification:**
  - New `get_verified_fills()` function checks BOTH order status AND position API
  - Uses `max()` across 3 sources: order status, position API, local tracking
  - Logs discrepancies: `DUAL_VERIFY: order=(0/5) api=(0/5) local=(0/0) -> (0/5)`
  - Added periodic health check every loop cycle to detect fills from order status
  - Shows `ORDER_FILL_DETECTED: UP 5.0 shares` when fill detected

### 2026-01-14
- Implemented confidence-based 99c capture strategy
- Fixed pairing mode bug (was triggering on 99c captures)
- Added fill notification for 99c captures
- Fixed micro-imbalance tolerance (0.1 → 0.5)
- Deployed to server, tested 99c capture
- One 99c capture trade lost (95% confidence @ 98c, T-69s)

### 2026-01-13
- Added Chainlink price feed integration
- Added order book imbalance analyzer
- Set up imbalance tracking/logging

---

## Verified Bot Rules & Settings (v1.56 "Danger Sense")

> **Last verified**: 2026-02-26 — local code and live server confirmed identical.
> **IMPORTANT**: Any future bot version MUST preserve these rules unless explicitly changed.
> These represent hard-won lessons from live trading losses and bugs.

### Active Strategy

| Setting | Value | Why |
|---------|-------|-----|
| `ARB_ENABLED` | `False` | ARB strategy disabled. Bot is **99c sniper only**. ARB was unprofitable and added risk. |
| `CAPTURE_99C_ENABLED` | `True` | 99c capture is the sole active strategy. |

### 99c Sniper Entry Rules

| Rule | Setting | Value | Lesson |
|------|---------|-------|--------|
| Min confidence | `CAPTURE_99C_MIN_CONFIDENCE` | `0.95` (95%) | Lower values lost money. A 95% trade lost once at 98c ask, T-69s. |
| Bid price | `CAPTURE_99C_BID_PRICE` | `0.95` (95c) | We bid at 95c, not 99c. Gets better fills. |
| Max ask | `CAPTURE_99C_MAX_ASK` | `1.01` | Skip if ask > 1.01 (no market). |
| Min time | `CAPTURE_99C_MIN_TIME` | `10` seconds | Need time for order to settle on blockchain. |
| One per window | `capture_99c_used` flag | Once | Prevents duplicate orders in same window. |

**Confidence formula**: `confidence = ask_price - time_penalty`

| Time Remaining | Penalty | Example: 99c ask = confidence |
|----------------|---------|-------------------------------|
| < 60 seconds | 0% | 99% |
| 60-120 seconds | 3% | 96% |
| 2-5 minutes | 8% | 91% |
| 5+ minutes | 15% | 84% |

**Entry filters** (`ENTRY_FILTER_ENABLED = True`):
- **Stability**: Last 3 ticks must ALL be >= 97c (prevents entering on spikes)
- **Low volatility**: No tick-to-tick jump > 8c in past 10 ticks
- **Opposing side low**: Opposing side never > 15c in past 30 ticks
- Logic: Enter if stable at 97c+ OR (low volatility AND opposing low)
- **WHY**: Analysis of historical losses showed entries during volatile spikes always lost.

### Trade Sizing

| Setting | Value | Why |
|---------|-------|-----|
| `TRADE_SIZE_PCT` | `0.42` (42%) | 42% of portfolio per trade. |
| Daily lock | Locked once per day | Prevents position size growing mid-day as portfolio grows from wins. Shares calculated on first window after midnight EST, frozen for 24h. |
| `CAPTURE_99C_MAX_SPEND` | `$6.00` | Fallback if portfolio balance API fails. |

### Hard Stop (Emergency Exit)

| Setting | Value | Why |
|---------|-------|-----|
| `HARD_STOP_ENABLED` | `True` | Always on. Last line of defense. |
| `HARD_STOP_TRIGGER` | `0.40` (40c best bid) | Exit when best bid <= 40c. Triggers on BID not ask (real liquidity). |
| `HARD_STOP_CONSECUTIVE_REQUIRED` | `2` ticks | **v1.54**: Requires 2 consecutive ticks below 40c. Prevents single-tick panic sells from momentary order book gaps. |
| `HARD_STOP_USE_FOK` | `True` | Fill-or-Kill market orders for guaranteed execution. |
| `HARD_STOP_FLOOR` | `0.01` (1c) | Will sell at ANY price. Better to get 10c than ride to $0. |
| Chunked FOK | v1.55 | **Sells in chunks sized to order book depth (90% of bid depth per attempt).** Prevents all-or-nothing FOK rejection when position > book depth. |
| Balance error detection | v1.55 | **Halves chunk on "not enough balance" errors, stops after 3.** Prevents blind retrying when shares are already sold. |

### Danger Exit — Confidence + Opponent Ask Gate (v1.56)

| Setting | Value | Why |
|---------|-------|-----|
| `DANGER_EXIT_ENABLED` | `True` | Exits when danger_score >= 0.40 AND opponent_ask > 15c. Catches reversals before bid collapse. |
| `DANGER_EXIT_THRESHOLD` | `0.40` | Danger score threshold. Uses existing 5-component danger score (confidence drop, OB imbalance, BTC velocity, opponent ask, time decay). |
| `DANGER_EXIT_OPPONENT_ASK_MIN` | `0.15` (15c) | **The key gate.** Winning trades NEVER have opponent ask > 8c. 15c provides 7c margin. On the one loss (2026-02-25), opponent was 65c. |
| `DANGER_EXIT_CONSECUTIVE_REQUIRED` | `2` ticks | Prevents single-tick noise. Same pattern as hard stop. |

**How it works:** After 99c capture fill, every tick the bot calculates a danger score from 5 weighted signals. If the score exceeds 0.40 AND the opponent's ask price is above 15c (confirming real market uncertainty, not just end-of-window settlement noise), the bot exits via `execute_hard_stop()` — while bids are still at 70-80c, not waiting until they collapse to 40c.

**Backtested on 2026-02-25/26:** Zero false exits on 13 winning windows. Would have caught the $382.69 loss.

### ROI Halt (Daily Profit Protection)

| Setting | Value | Why |
|---------|-------|-----|
| `ROI_HALT_THRESHOLD` | `0.60` (60%) | Stop trading when daily ROI hits 60%. Lock in profits, don't give back. |
| Reset | Midnight EST | Cron job deletes state file. Bot self-corrects stale halt at startup. |

### Disabled Features (DO NOT RE-ENABLE without good reason)

| Feature | Setting | Why Disabled |
|---------|---------|--------------|
| OB Early Exit | `OB_EARLY_EXIT_ENABLED = False` | Was causing premature exits. Only hard stop at 40c now. |
| Price Stop Loss | `PRICE_STOP_ENABLED = False` | Legacy. Replaced by hard stop which uses FOK orders. |
| 99c Hedge | `CAPTURE_99C_HEDGE_ENABLED = False` | Was triggering on normal end-of-window price death (50c/1c is expected at resolution). |
| Smart Signals | `USE_SMART_SIGNALS = False` | Signals were 50/50 accuracy. Trade on price divergence alone. |
| ARB Strategy | `ARB_ENABLED = False` | Unprofitable. 99c sniper only. |

### Failsafe Price Limits (NEVER VIOLATE)

| Limit | Value | Notes |
|-------|-------|-------|
| `FAILSAFE_MAX_BUY_PRICE` | `0.85` (85c) | 99c capture BYPASSES this (intentionally above 85c). |
| `FAILSAFE_MIN_BUY_PRICE` | `0.05` (5c) | Never buy garbage prices. |
| Max shares/cost | `999999` | No practical limit (portfolio-sized trades). |

### Timing Constants

| Setting | Value | Purpose |
|---------|-------|---------|
| `MIN_TIME_FOR_ENTRY` | `300` (5 min) | Never enter ARB with <5 min remaining. |
| `ORDER_COOLDOWN_SECONDS` | `3.0` | Prevent rapid-fire duplicate orders. |
| `ORDER_SETTLE_DELAY` | `7.0` | Wait for blockchain settlement after order. |
| `PAIR_DEADLINE_SECONDS` | `90` | Start forced pairing 90s before close. |
| `HARD_FLATTEN_SECONDS` | `10` | Emergency flatten at 10s before close. |
| `BAIL_TIME_REMAINING` | `90` | Force bail at 90s remaining (NO EXCEPTIONS). |
| `PAIR_WINDOW_SECONDS` | `5` | If second leg doesn't fill in 5s, bail immediately. |

### Position Tracking (Critical Bug Fixes)

| Rule | Why |
|------|-----|
| Positions can only INCREASE, never decrease | API returns stale 0/0 sometimes. If we know we have fills, never overwrite with lower values. |
| Dual-source verification | Check BOTH order status AND position API, use `max()` across all sources. |
| `MICRO_IMBALANCE_TOLERANCE = 0.5` | Treats <0.5 share difference as balanced. Fixed bug where 4.9 vs 5.0 triggered rebalancing. |
| 99c captures excluded from ARB imbalance | `get_arb_imbalance()` subtracts 99c fills. Prevents false PAIRING_MODE entry. |
| Don't set fill counts at order time | Fill tracking happens in main loop detection ONLY. Setting at order time causes phantom negative imbalance. |

### Order Book Signals (Logging Only)

| Setting | Value | Notes |
|---------|-------|-------|
| `USE_ORDERBOOK_SIGNALS` | `True` | **Logging only** — signals do NOT affect trading decisions. |
| Accuracy | ~50% | Signals were wrong as often as right. Kept for data collection only. |

### Gas Monitoring

| Threshold | Value | Action |
|-----------|-------|--------|
| `GAS_LOW_THRESHOLD` | `0.1` MATIC | Telegram warning (~4 days of gas). |
| `GAS_CRITICAL_THRESHOLD` | `0.03` MATIC | Telegram CRITICAL alert (~1 day of gas). |
| Alert cooldown | 1 hour | Prevent spam. |

### Common Mistakes to Avoid

1. **Don't set capture_99c_filled_up/down at order time** — only on confirmed fill. Setting early causes `get_arb_imbalance()` to return a phantom negative, triggering false PAIRING_MODE.
2. **Don't trust API position counts to decrease** — API caches are stale. Only use `max(local, api)`.
3. **Don't use `slug` variable in `check_and_place_arb()`** — use `window_state.get('window_id', '')`. Slug is only defined in `main()`.
4. **Don't re-enable 99c hedge** — end-of-window price death (50c/1c) is NORMAL at resolution. Hedging against it wastes money.
5. **Don't run long SSH one-liners to start the bot** — they break due to line wrapping. Always SSH in interactively.
6. **Don't use public polygon-rpc.com** — it rate-limits. Use Alchemy RPC from `POLYGON_RPC` env var.
7. **Don't lower HARD_STOP_CONSECUTIVE_REQUIRED below 2** — single-tick gaps in order book caused panic sells when set to 1.
8. **Don't try to FOK sell more shares than the order book can absorb** — 234 shares in a thin book = 100% kill rate. Always chunk FOK sells to order book depth.
9. **Don't remove the opponent_ask gate from danger exit** — danger score alone causes false exits. Winning trades score 3.01-3.16 due to end-of-window settlement, while the actual loss scored only 2.09. The `opponent_ask > 15c` gate is what distinguishes real reversals from normal settlement noise.
10. **Don't rely on bid collapse (40c) as primary exit signal** — by the time bids hit 40c, actual FOK fills are at ~13-26c. Use leading indicators (opponent ask + danger score) to exit while bids are still at 70-80c. Keep 40c hard stop only as absolute backstop.

---

## Quick Reference

```
To hit 95% confidence (current threshold):
  - 95c ask @ any time = 95% (just barely triggers)
  - 98c ask @ <60s remaining = 98%
  - 99c ask @ <60s remaining = 99%
  - 99c ask @ 60-120s = 96% (triggers)
  - 98c ask @ 2-5min = 90% (does NOT trigger)

Current threshold: 95%
Current bid price: 95c
Trade size: 42% of portfolio (locked daily)
Hard stop: 40c best bid (2 consecutive ticks, chunked FOK)
ROI halt: 60% daily
```
