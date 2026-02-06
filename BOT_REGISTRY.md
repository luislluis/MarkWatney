# Polybot Version Registry

## Current Version: v1.55 "Iron Fist"

| Version | DateTime | Codename | Changes | Status |
|---------|----------|----------|---------|--------|
| v1.55 | 2026-02-06 PST | Iron Fist | OB exit now uses FOK market orders (was limit sell). Atomic execution, no partials, no stale orders left on book. | Active |
| v1.54 | 2026-02-06 PST | Clean Slate | Strip ALL ARB/PAIRING code (~1200 lines removed). Clean 99c sniper-only bot. No more phantom PAIRING_MODE triggers. 3408→2184 lines. | Archived |
| v1.53 | 2026-02-06 PST | Dead Gate | CRITICAL: Block PAIRING_MODE when ARB disabled. Stale position API triggered phantom imbalances causing ~$6 loss in 99c-only mode. Two gates: entry check + state handler guard. | Archived |
| v1.52 | 2026-02-05 PST | Iron Veil | 15-agent audit hardening: BTC safety on hard stop, empty-book danger guard, position re-inflation fix, partial OB exit fix, allowance/balance separation, realized_pnl tracking, RTDS disconnect safety, state-before-log ordering, stale books cleanup. | Archived |
| v1.51 | 2026-02-05 PST | True North | Fix: Suppress false danger exits when BTC safely on our side ($30+ margin). Stop cascading hard stop re-triggers (mark exited after any attempt). | Archived |
| v1.50 | 2026-02-05 PST | Iron Shield II | Fix: FOK status case-sensitivity (matched vs MATCHED). Stop retry on not-enough-balance (shares already sold). | Archived |
| v1.49 | 2026-02-05 PST | Iron Shield | 8 exit hardening fixes: danger score exit trigger (0.8 in final 30s), empty bids triggers hard stop, API-down fallback to stale books, OB exit 0-fill protection, OB counter decay, book refresh in hard stop retry, skip API timeout in exits. | Archived |
| v1.48 | 2026-02-05 PST | Last Second | Remove 15s dead zone: exits (HARD STOP, OB EXIT) now active until T-0. CLOSE_GUARD only blocks new entries. WINDOW COMPLETE shows after settlement with result. | Archived |
| v1.47 | 2026-02-05 PST | Steel Gate | Fix: Stop false PAIRING_MODE entry from 99c captures. Don't set capture_99c_filled at placement, add max(0) guard on ARB imbalance calc. | Archived |
| v1.46 | 2026-02-05 PST | Iron Lock | Fix: Lock capture_99c_used BEFORE API call to prevent duplicate order spam when API fails | Archived |
| v1.45 | 2026-02-05 PST | Granite Wolf | Audit cleanup: Remove dead ARB constants, hedge system, stale CHATGPT references. Fix effective_floor crash bug. Rename to MarkWatney. | Archived |
| v1.44 | 2026-02-05 PST | Silent Puma | Remove: 5-SEC RULE auto-bail from pairing mode (legacy v1.10 code that should no longer fire) | Archived |
| v1.43 | 2026-02-05 PST | Steady Falcon | Fix: OB EXIT sell uses actual API position (5.9995) not tracked (6.0) to avoid 'not enough balance' rejections. Remove unused strategy_signals imports. | Archived |
| v1.42 | 2026-02-05 PST | Crystal Dashboard | Fix: Dashboard uses CAPTURE_FILL (not CAPTURE_99C) so unfilled orders don't show as pending, prices show correctly | Archived |
| v1.37 | 2026-02-04 PST | Data Driven | Log CAPTURE_99C_WIN/LOSS events for Supabase dashboard tracking | Archived |
| v1.36 | 2026-02-03 PST | No Harm No Foul | Place 99c bids even when ask >= 99c - if doesn't fill, no loss | Archived |
| v1.35 | 2026-02-03 PST | Six Shooter | Increase 99c capture to 6 shares ($6 max spend) | Archived |
| v1.34 | 2026-02-03 PST | Iron Exit | 60¢ hard stop: FOK market orders for guaranteed emergency exit | Archived |
| v1.33 | 2026-01-28 PST | Phoenix Feed | RTDS WebSocket: Real-time BTC prices from Polymarket's Chainlink stream | Archived |
| v1.32 | 2026-01-27 PST | Gas Guardian | MATIC balance monitoring: Log at window start, bold Telegram alert when low | Archived |
| v1.31 | 2026-01-26 PST | Background Flush | Non-blocking flush: Sheets/Supabase uploads run in background threads | Archived |
| v1.30 | 2026-01-26 PST | Confidence Display | Show 99c confidence in tick output (DN:49%/95%) + activity logging to Supabase | Archived |
| v1.29 | 2026-01-26 PST | Activity Stream | Activity logging to Supabase with market prices (up_ask, down_ask, ttl) | Archived |
| v1.28 | 2026-01-26 PST | Price Guardian | Price stop-loss: Exit when price ≤ 80c, floor at 50c (never lose >50%) | Archived |
| v1.27 | 2026-01-26 PST | Critical Shield | Skip Sheets flush during final 60s to prevent blocking during critical trading | Archived |
| v1.26 | 2026-01-25 PST | Supabase Stream | Add Supabase real-time tick logging + fix 99c outcome detection | Archived |
| v1.25 | 2026-01-26 PST | OB Guardian | 99c OB-based early exit: Exit when OB < -0.30 for 3 consecutive ticks | Archived |
| v1.24 | 2026-01-24 PST | Rich Purnell | Smart 99c sniper: Entry filters (stability/volatility) + disabled end-of-window hedge trigger | Archived |
| v1.23 | 2026-01-24 PST | Hexadecimal | Fix: Silent exceptions logged (10 locations) + council backup system | Archived |
| v1.22 | 2026-01-24 PST | Pathfinder Data | Fix: P&L logging for EARLY_BAIL and 99C_HEDGE now records actual losses | Archived |
| v1.21 | 2026-01-24 PST | Ares III Council | AI Council system: 3 AI models analyze daily trades, debate, vote | Archived |
| v1.20 | 2026-01-24 PST | Signal Hawk | 99c sniper Telegram notifications: fill alerts + win/loss + daily summary | Archived |
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

### v1.34 - Iron Exit (2026-02-03)
*"Never ride to zero."*
- **60¢ Hard Stop with FOK Market Orders**
  - Triggers when best bid ≤ 60¢ (not ask price)
  - Uses Fill-or-Kill (FOK) market orders for guaranteed execution
  - No price floor - will sell at any price (1¢ > $0)
  - Keeps selling until position is completely flat
- **Why Best Bid?**
  - Empty order books show phantom 50¢ prices with no liquidity
  - Best bid only exists if someone is actually buying
  - Prevents false triggers from empty markets
- **Replaces Legacy Price Stop**
  - Old: PRICE_STOP_TRIGGER = 80¢, PRICE_STOP_FLOOR = 50¢, limit orders
  - New: HARD_STOP_TRIGGER = 60¢, HARD_STOP_FLOOR = 1¢, FOK market orders
- **Max Loss Protection**
  - Entry at 99¢ → 60¢ trigger = 39% max drawdown
  - Historical: 5 losses went to $0, each costing ~$4.90
  - With hard stop: worst case ~$1.95 per loss
- **Files changed:** trading_bot_smart.py

### v1.33 - Phoenix Feed (2026-01-28)
*"Rising from the ashes of stale prices"*
- **RTDS WebSocket Integration**
  - Connects to `wss://ws-live-data.polymarket.com`
  - Same price feed Polymarket uses for settlement
  - ~100ms latency (vs 1-60 seconds from on-chain)
- **Price to Beat Tracking**
  - Captures BTC price at each 15-minute window start
  - Shows delta in status line: `BTC:$89,200(+$52)`
  - Positive = UP winning, Negative = DOWN winning
- **Graceful Fallback**
  - Falls back to Chainlink on-chain if RTDS disconnects
- **Files changed:** rtds_price_feed.py (NEW), trading_bot_smart.py

### v1.32 - Gas Guardian (2026-01-27)
*"Never run out of gas."*
- **MATIC Balance Monitoring**
  - Checks EOA gas balance at every window start
  - Logs balance and days remaining: `⛽ Gas: 0.1234 MATIC (4.6 days) [OK]`
  - Status indicators: OK, LOW, CRITICAL
- **Telegram Alerts**
  - **LOW** (< 0.1 MATIC, ~4 days): Yellow warning with balance and EOA address
  - **CRITICAL** (< 0.03 MATIC, ~1 day): Red alert with instructions to fund
  - Alert cooldown: 1 hour to avoid spam
- **Thresholds**
  - `GAS_LOW_THRESHOLD = 0.1 MATIC` (~4 days of gas)
  - `GAS_CRITICAL_THRESHOLD = 0.03 MATIC` (~1 day of gas)
- **Files changed:** trading_bot_smart.py

### v1.31 - Background Flush (2026-01-26)
*"Never block the main loop."*
- **Non-blocking flush operations**
  - Sheets and Supabase flush operations now run in background threads
  - Main loop no longer waits for API calls to complete
  - Eliminates 10-12 second pauses during flush operations
  - Buffer is copied and cleared immediately, upload happens asynchronously
  - **Fix:** `_ensure_initialized()` moved to startup (was blocking before thread start)
  - Connection established once at init, not on every flush call
- **Files changed:** sheets_logger.py, supabase_logger.py

### v1.30 - Confidence Display (2026-01-26)
*"Know your odds at a glance."*
- **99c Confidence in Tick Output**
  - New display format: `DN:49%/95%` shows leading side, current confidence, and threshold
  - Always visible so you can track how close the bot is to triggering 99c capture
  - Example: `DN:49%/95%` = DOWN side at 49% confidence (needs 95% to trigger)
- **Activity Logging to Supabase** (from v1.29)
  - All bot activity buffered and sent to Supabase
  - SMART_SKIP and SMART_TRADE_APPROVED include up_ask, down_ask, ttl
- **Files changed:** trading_bot_smart.py

### v1.29 - Activity Stream (2026-01-26)
*"Every action tells a story."*
- **Activity Logging to Supabase**
  - All bot activity now buffered and sent to Supabase "Activity" table
  - Non-blocking: uses buffer/flush pattern like tick logging
  - Activity data includes market context: `up_ask`, `down_ask`, `ttl`
  - SMART_SKIP and SMART_TRADE_APPROVED events include prices
- **Files changed:** trading_bot_smart.py, supabase_logger.py

### v1.28 - Price Guardian (2026-01-26)
*"When the price says run, you run."*
- **Price Stop-Loss for 99c Capture**
  - **Trigger at 80c**: When our side's price drops to 80c or below, immediately exit
  - **Floor at 50c**: Never sell below 50c (never lose more than 50% of the trade)
  - Reuses existing `execute_99c_early_exit()` function with new `reason` parameter
  - Differentiates between "price_stop" (reactive) and "ob_reversal" (proactive) exits
  - Different Telegram notifications and Sheets event types for each reason
  - **Files changed:** trading_bot_smart.py
  - **Priority order in main loop:** Hard floor (50c) → Price stop (80c) → OB exit (-0.30 imbalance)

### v1.27 - Critical Shield (2026-01-26)
*"The main loop must never be blocked during critical periods."*
- **Fix: Skip Sheets flush during final 60 seconds**
  - Root cause: $4.70 loss when Sheets flush blocked main loop for 12 seconds during market crash
  - The bot couldn't detect price reversal because it was frozen uploading tick data
  - Solution: `maybe_flush_ticks(ttl)` now skips if TTL < 60 seconds
  - Ticks buffer until after the critical period, then flush normally
- **Files changed:** sheets_logger.py, supabase_logger.py, trading_bot_smart.py
- **Impact:** Zero blocking during the critical final minute of each window

### v1.26 - Supabase Stream (2026-01-25)
*"Real-time visibility into every tick."*
- Add Supabase real-time tick logging alongside Google Sheets
- Fix 99c outcome detection

### v1.25 - OB Guardian (2026-01-26)
*"Trust the order book, not the price."*
- **99c OB-Based Early Exit**
  - Exit 99c positions when order book imbalance < -0.30 for 3 consecutive ticks
  - Protects against reversals before they show in price
  - Minimum exit price floor of 70c to prevent panic sells

### v1.24 - Rich Purnell (2026-01-24)
*"I'm going to need to math the shit out of this."*
- **Smart 99c Sniper Entry Filters** (Based on tick data analysis)
  - **STABLE filter**: Only enter if last 3 ticks all >= 97c (sustained confidence, not spike)
  - **LOW VOLATILITY filter**: Skip if any tick-to-tick jump > 8c in past 10 ticks
  - **OPPOSING LOW filter**: Skip if opposing side was > 15c in past 30 ticks
  - Pattern analysis showed: 100% of losses had volatile entries, 0% of stable entries lost
- **Disabled End-of-Window Hedge Trigger**
  - Problem: Hedge was firing when market "dies" at window end (prices drop to 50c/1c)
  - This wasn't a real reversal - just end-of-window liquidity death
  - Set `CAPTURE_99C_HEDGE_ENABLED = False`
- **New tracking**: `market_price_history` deque stores (timestamp, up_ask, down_ask) for 30 ticks
- **Expected improvement**: From -$12 net P&L to +$8 net P&L

### v1.23 - Hexadecimal (2026-01-24)
*"How Watney communicated with NASA using 16 characters."*
- Fix: 10 silent exception blocks (`except: pass`) now log errors with descriptive messages
- Added council backup system: saves to local JSON before Google Docs API call

### v1.22 - Pathfinder Data (2026-01-24)
*"First communication from Mars - now we can see the losses."*
- Fix: P&L logging for EARLY_BAIL and 99C_HEDGE now records actual loss amounts
- Previously only logged wins, making AI Council analysis inaccurate

### v1.21 - Ares III Council (2026-01-24)
*"Five crew members. Three AI minds. One mission."*
- **AI Council Trading Analysis System**
- Daily analysis from Claude (Watney), ChatGPT (Johanssen), Gemini (Martinez)
- Each AI adopts a character persona from The Martian
- **Phase 1: Independent Analysis**
  - Each AI analyzes trade data without seeing others' responses
  - Identifies patterns in wins/losses, risk factors, strategy recommendations
- **Phase 2: Debate & Rebuttals**
  - Each AI sees others' analyses and responds
  - Points of agreement, disagreement, missed insights
- **Phase 3: Final Vote**
  - Each AI casts vote on #1 recommended change
  - Includes reasoning, implementation steps, confidence level
- **Output & Automation**
  - Full transcript saved to Google Doc
  - Telegram notification with summary and doc link
  - Runs daily via cron at 6 AM PST
  - Only analyzes trades since last council session
- **Data Accuracy**
  - Fetches directly from Google Sheets (Windows + Events)
  - Validates timestamps and filters by last council date
  - Shows sample size warnings for small datasets
- **New File:** `ai_council.py`

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

**The Martian**: Ares III Council, Watney's Orphans, Pathfinder, Sol 549, Ares III, Iron Man Maneuver, The Potato Farmer

**Ocean's Trilogy**: The Bellagio Job, The Night Fox, Rusty's Cut

**Billions**: Dollar Bill's Recovery, Wendy's Dashboard, The Enforcer, Axe's Edge, The Performance Coach, Short Squeeze Sunday, Ice Juice, Chuck's Gambit

**Forgetting Sarah Marshall**: Turtle Bay, Dracula Musical
