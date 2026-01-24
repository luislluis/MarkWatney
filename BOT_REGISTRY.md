# Polybot Version Registry

## Current Version: v1.20 "Signal Hawk"

| Version | DateTime | Codename | Changes | Status |
|---------|----------|----------|---------|--------|
| v1.20 | 2026-01-24 PST | Signal Hawk | 99c sniper Telegram notifications: fill alerts + win/loss + daily summary | Active |
| v1.19 | 2026-01-24 PST | Laser Falcon | ARB disabled - 99c sniper only mode | Archived |
| v1.18 | 2026-01-22 PST | Watney's Orphans | Fix: Orphaned startup shares excluded from ARB imbalance (no more false BAIL loops) | Archived |
| v1.17 | 2026-01-22 PST | The Bellagio Job | Fix: 99c outcome via CLOB API resolution, background checker, activity log sheet, dashboard merge fix | Archived |
| v1.16 | 2026-01-21 PST | Dollar Bill's Recovery | Fix: STATE_DONE recovery - detect and handle ARB imbalances that slipped through | Archived |
| v1.15 | 2026-01-21 PST | Wendy's Dashboard | Add performance dashboard logging for ARB (PAIRED/BAIL/LOPSIDED) and 99c capture trades | Archived |
| v1.14 | 2026-01-21 PST | The Night Fox | Fix: Remove retry from place_limit_order (caused duplicate orders bypassing dedup check) | Archived |
| v1.13 | 2026-01-21 PST | Pathfinder | 99c capture 403 recovery + clean Cloudflare error messages (no HTML spam) | Archived |
| v1.12 | 2026-01-21 PST | Sol 549 | 99c capture recovery from 403 errors via position polling + retry logic | Archived |
| v1.11 | 2026-01-21 PST | Rusty's Cut | Fix: Record fill prices for second leg in pairing mode + log 99c capture bid_price | Archived |
| v1.10 | 2026-01-20 PST | The Enforcer | 5-second rule: if second ARB leg doesn't fill in 5s, bail immediately | Archived |
| v1.9 | 2026-01-20 PST | Axe's Edge | OB-based early bail: detect ARB reversals via order book before price moves (target <10c loss) | Archived |
| v1.8 | 2026-01-19 20:30 PST | Turtle Bay | 99c capture: skip entry when ask >= 99c (avoids reversal traps) | Archived |
| v1.7 | 2026-01-19 14:00 PST | The Performance Coach | Observability: danger score in Ticks, signal breakdown in hedge events | Archived |
| v1.6 | 2026-01-19 13:30 PST | Short Squeeze Sunday | Hedge execution: replace confidence trigger with danger score >= 0.40 | Archived |
| v1.5 | 2026-01-19 13:00 PST | Ice Juice | Danger scoring engine: 5-signal weighted scoring system | Archived |
| v1.4 | 2026-01-19 12:30 PST | Ares III | Tracking infrastructure: peak confidence, price velocity | Archived |
| v1.3 | 2026-01-16 01:00 PST | Dracula Musical | Fix: Cancel race condition - track pending hedge order IDs | Archived |
| v1.2 | 2026-01-16 00:30 PST | Chuck's Gambit | Fix: PAIRING_MODE race condition causing duplicate orders | Archived |
| v1.1 | 2026-01-15 21:58 PST | Iron Man Maneuver | Auto-redeem: direct CTF contract redemption through Gnosis Safe | Archived |
| v1.0 | 2026-01-15 20:50 PST | The Potato Farmer | Baseline - includes PAIRING_MODE hedge escalation + 99c capture hedge protection | Archived |

## Version History Details

### v1.20 - Signal Hawk (2026-01-24)
*"Eyes in the sky, reporting every move."*
- **99c Sniper Telegram Notifications**
- Fill notification: Sends alert when 99c order fills with side, shares, confidence, time left
- Resolution notification: Sends win/loss result when window closes with P&L
- Daily rolling summary: Shows wins, losses, total P&L, win rate, ROI % after each trade
- Added `sniper_stats` tracking for session-wide 99c performance
- Added `check_99c_outcome()` function to determine win/loss from final market prices

### v1.19 - Laser Falcon (2026-01-24)
*"I can hit a target from 2 miles away."*
- **ARB strategy disabled - 99c sniper only mode**
- Added `ARB_ENABLED = False` flag to disable arbitrage trading
- 99c capture strategy remains fully active
- Bot will only trade 99c sniper opportunities (confidence >= 95%)
- ARB can be re-enabled by setting `ARB_ENABLED = True`
- Goal: Perfect the 99c strategy before re-enabling ARB

### v1.18 - Watney's Orphans (2026-01-22)
*"I'm going to have to science the shit out of this."*
- **Orphaned startup shares excluded from ARB imbalance**
- Bug: Bot started with leftover shares from previous window (e.g., 5 DOWN from 16m ago)
- When 99c capture filled, bot saw position as 5 UP + 5 DOWN
- ARB imbalance calculation: (5-5 capture) - (5-0 capture) = -5 → triggered false BAIL loops
- BAIL kept failing because order book was empty near window end
- Fix: Track `orphaned_up_shares` and `orphaned_down_shares` from startup sync
- ARB imbalance now excludes: 99c capture shares AND orphaned startup shares
- Bot no longer enters PAIRING_MODE for orphaned shares
- Orphaned shares will expire naturally or win/lose based on market outcome

### v1.17 - The Bellagio Job (2026-01-22)
*"The house always wins. Unless you change the rules."*
- **99c capture outcome via CLOB API resolution**
- Previous bug: Order books empty at window end → all 99c captures marked as LOSS
- Fix: Now uses Polymarket CLOB API to get actual market resolution
- Calls `https://clob.polymarket.com/markets/{conditionId}` to find winner
- **Background resolution checker**
- 99c captures queued at window end, not resolved immediately
- Background thread checks every 60 seconds for pending resolutions
- Non-blocking: bot continues trading while waiting for resolution
- **Dashboard row merge fix**
- Previous bug: Logging ARB then 99c would overwrite ARB data with empty cells
- Fix: Reads existing row, merges new data, writes merged result
- **Activity log sheet**
- New "Activity" sheet logs every order/fill
- Columns: Timestamp, Window, Action, Side, Price, Shares, Total, OrderID, Details
- Actions: ORDER_BUY, ORDER_SELL, ORDER_FAILED, FILL

### v1.16 - Dollar Bill's Recovery (2026-01-21)
*"What have I always told you? Dollar Bill always survives."*
- **Fix: STATE_DONE recovery for missed ARB imbalances**
- Bug: Bot could get stuck showing "IMBAL" status without entering PAIRING_MODE
- Root cause: When API sync temporarily showed balanced (stale data), state was set to STATE_DONE
- Once in STATE_DONE, subsequent imbalance detection was bypassed (just `pass` in main loop)
- Result: No BAIL or HARD_FLATTEN triggered, position left unhedged
- Fix: Added recovery check in STATE_DONE that detects ARB imbalances and re-enters PAIRING_MODE
- Now every loop iteration checks for imbalance, even in STATE_DONE
- Triggers: "RECOVERY: STATE_DONE but ARB imbalance=X, T-Xs - re-entering PAIRING_MODE"

### v1.15 - Wendy's Dashboard (2026-01-21)
*"I'm here to help you perform at your best."*
- **Performance dashboard integration**
- Trading bot now logs directly to Google Sheets performance dashboard
- Logs ARB trades: PAIRED (profit), BAIL (loss from early exit), LOPSIDED (loss from failed pairing)
- Logs 99c capture hedges: LOSS (when danger score triggers hedge)
- Fixes issue where performance tracker missed trades that completed before it started
- Dashboard at: https://docs.google.com/spreadsheets/d/18bCu_op6oGVVQ9DFGW6oJjbZj_HdJYQ5UlW87iVKbCU

### v1.14 - The Night Fox (2026-01-21)
*"You think he's the best? I'm the best."*
- **Fix: Remove retry from place_limit_order**
- Bug: Retry logic was bypassing duplicate order detection in place_and_verify_order()
- Sequence: 1) Duplicate check passes 2) Order placed 3) 403 returned 4) Retry places DUPLICATE
- Fix: Removed retry entirely, rely on position polling for 403 recovery instead

### v1.13 - Pathfinder (2026-01-21)
*"Mark, this is Vincent Kapoor. We're going to get you home."*
- **Clean Cloudflare error messages + 99c capture 403 recovery**
- Problem: Cloudflare 403 blocks dumped entire HTML page to logs (hundreds of lines)
- Added `clean_api_error()` helper to detect and clean Cloudflare responses
- Errors now show: `CLOUDFLARE_403_BLOCK (Ray:abc123)` instead of full HTML
- All 403/Cloudflare errors trigger retry with 2-second delay
- Recovery polling uses cleaned error format for detection

### v1.12 - Sol 549 (2026-01-21)
*"In your face, Neil Armstrong."*
- **99c capture recovery from Cloudflare 403 errors**
- Problem: Cloudflare intermittently returns 403 errors for order placement
- ARB orders survive 403s via DUAL_VERIFY, polling, and health checks
- 99c capture orders had no recovery mechanism - they failed silently
- Fixes:
  1. `place_limit_order()` - Retry once on 403 with 2-second delay
  2. `execute_99c_capture()` - On 403, poll position API for 5 seconds to detect fills
  3. Main loop health check - Position-based fallback detection even without order_id
- Now 99c captures are as resilient as ARB orders to 403 errors
- Baseline position tracking (`pre_capture_up/down_shares`) enables recovery detection

### v1.11 - Rusty's Cut (2026-01-21)
*"You'd need at least a dozen guys doing a combination of cons."*
- **Dashboard accuracy fix: Fill price recording**
- Bug 1: CAPTURE_99C events logged ask_price (market price) instead of bid_price (order price of 99c)
  - Fixed by adding `bid_price=CAPTURE_99C_BID_PRICE` to CAPTURE_99C event logging
- Bug 2: Pairing mode fills didn't record second leg fill price
  - Added price recording in 4 locations where second leg fills
- These fixes ensure performance dashboard shows exact entry prices for all trades

### v1.10 - The Enforcer (2026-01-20)
*"I don't care what it takes. Get it done."*
- **5-second rule for ARB pairing**
- `PAIR_WINDOW_SECONDS = 5` - If second leg doesn't fill in 5 seconds, bail immediately
- Observation: Most successful ARB pairs complete within 5 seconds
- After 5 seconds without pairing, take best available bail price immediately
- Simplifies logic: no more waiting 30+ seconds hoping for better prices

### v1.9 - Axe's Edge (2026-01-20)
*"I'm not uncertain. I'm never uncertain."*
- **OB-based early bail for ARB strategy**
- Detects reversals via order book imbalance before price moves significantly
- `OB_REVERSAL_THRESHOLD = -0.25` - Bail when filled side has 25%+ selling pressure
- `OB_REVERSAL_PRICE_CONFIRM = 0.03` - Only need 3c price drop when OB confirms
- Target: <10c loss instead of 23c
- Runs during first 15 seconds of PAIRING_MODE when reversals are most likely

### v1.8 - Turtle Bay (2026-01-19)
*"When life gives you lemons, just say 'fuck the lemons' and bail."*
- **99c capture reversal trap prevention**
- Added `CAPTURE_99C_MAX_ASK = 0.99` threshold
- If ask price >= 99c, skip the 99c capture entirely
- Rationale: When ask is at 99c+, our 99c bid is at or below ask
- A fill means price dropped TO our bid = catching a falling knife
- When ask < 99c, our bid is above ask = immediate fill, safe entry

### v1.7 - The Performance Coach (2026-01-19)
*"What do you want? Not what does Axe want. What do YOU want?"*
- **Full observability for danger score system**
- Danger score (D:X.XX) displayed in console output every tick
- DangerScore column added to Google Sheets Ticks
- Signal breakdown logged on hedge events (confidence, velocity, OB, opponent, time)

### v1.6 - Short Squeeze Sunday (2026-01-19)
*"We're going to squeeze them until they beg for mercy."*
- **Danger score triggers hedge instead of simple confidence threshold**
- Replace 85% confidence check with danger_score >= 0.40
- More nuanced triggering using multiple signals

### v1.5 - Ice Juice (2026-01-19)
*"The setup is everything. The execution is just math."*
- **Multi-signal danger scoring engine**
- 5 weighted signals: confidence drop (3.0), OB imbalance (0.4), velocity (2.0), opponent ask (0.5), time decay (0.3)
- Returns both raw values and weighted components

### v1.4 - Ares III (2026-01-19)
*"Five crew members. One mission. No room for error."*
- **Tracking infrastructure for danger scoring**
- Peak confidence tracking per 99c capture position
- BTC price velocity tracking (5-second rolling window)
- Foundation for v1.5 danger scoring

### v1.3 - Dracula Musical (2026-01-16)
*"It's a comedy, but with darkness. And teeth."*
- **Bug fix**: Cancel race condition causing duplicate hedge orders
- Track pending_hedge_order_id in window_state
- Before placing new hedge, check if previous order was filled (despite cancel)
- Prevents duplicates like 10 UP / 5 DOWN when hedging

### v1.2 - Chuck's Gambit (2026-01-16)
*"I don't bend the law. I use it."*
- **Bug fix**: Race condition in PAIRING_MODE causing duplicate orders
- Added position re-verification after cancel_all_orders()
- Prevents placing new order if original order already filled

### v1.1 - Iron Man Maneuver (2026-01-15)
*"I know what I have to do. The math checks out."*
- **Auto-redeem feature** for winning positions
- Direct CTF contract redemption via Gnosis Safe execTransaction
- Detects resolved markets with winning positions
- Automatically claims USDC from winning outcome tokens
- Test mode: `test_redeem_detection()` for dry-run

### v1.0 - The Potato Farmer (2026-01-15)
*"I'm going to have to science the shit out of this."*
- **Baseline release** with all current features
- ARB trading strategy (buy both sides when combined < $1)
- 99c capture strategy (confidence-based single-side bets)
- PAIRING_MODE with time-based hedge escalation
- 99c capture hedge protection (auto-hedge on confidence drop)
- Google Sheets logging
- Dual-source position verification
- Chainlink price feed integration
- Order book imbalance analysis

---

## Codename Inspiration

**The Martian**: Watney's Orphans, Pathfinder, Sol 549, Ares III, Iron Man Maneuver, The Potato Farmer

**Ocean's Trilogy**: The Bellagio Job, The Night Fox, Rusty's Cut

**Billions**: Dollar Bill's Recovery, Wendy's Dashboard, The Enforcer, Axe's Edge, The Performance Coach, Short Squeeze Sunday, Ice Juice, Chuck's Gambit

**Forgetting Sarah Marshall**: Turtle Bay, Dracula Musical
