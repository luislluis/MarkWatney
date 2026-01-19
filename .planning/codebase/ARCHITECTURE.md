# Architecture

**Analysis Date:** 2026-01-19

## Pattern Overview

**Overall:** Event-Driven State Machine with Timed Windows

**Key Characteristics:**
- Single-process trading bot with 15-minute window lifecycle
- State machine transitions: QUOTING -> PAIRING_MODE -> DONE
- Real-time market data polling with aggressive completion patterns
- Multi-source position verification (order status + API + local tracking)

## Layers

**Core Trading Logic:**
- Purpose: Main arbitrage trading strategy and state management
- Location: `trading_bot_smart.py`
- Contains: State machine, order placement, pairing logic, bail/hedge decisions
- Depends on: py-clob-client, sheets_logger, chainlink_feed, orderbook_analyzer
- Used by: Entry point (main())

**Price Feeds:**
- Purpose: Get authoritative BTC price for settlement reference
- Location: `chainlink_feed.py`
- Contains: Chainlink on-chain oracle integration via Web3
- Depends on: web3 library, Ethereum RPC endpoints
- Used by: trading_bot_smart.py for price display and smart signals

**Order Book Analysis:**
- Purpose: Detect momentum and imbalance signals from bid/ask depth
- Location: `orderbook_analyzer.py`
- Contains: OrderBookAnalyzer class, imbalance calculations, signal generation
- Depends on: None (pure Python)
- Used by: trading_bot_smart.py for trade signal confirmation

**Logging & Persistence:**
- Purpose: Log trading events, ticks, and windows to Google Sheets
- Location: `sheets_logger.py`
- Contains: SheetsLogger class, batch tick buffering, retry logic
- Depends on: gspread, google-auth
- Used by: trading_bot_smart.py for all event logging

**Position Monitoring:**
- Purpose: Detect and redeem resolved winning positions
- Location: `auto_redeem.py`
- Contains: Position detection, CTF contract redemption, Gnosis Safe transactions
- Depends on: web3, eth-account, requests
- Used by: trading_bot_smart.py at window end, can run standalone

**Research/Analysis:**
- Purpose: Track order book imbalance correlation with outcomes
- Location: `imbalance_tracker.py`
- Contains: Data collection loop, correlation analysis, summary generation
- Depends on: orderbook_analyzer.py
- Used by: Run standalone for research

## Data Flow

**Trading Cycle (every ~500ms-2s):**

1. `get_current_slug()` - Calculate current 15-min window ID from Unix timestamp
2. `get_market_data()` - Fetch market metadata from Polymarket gamma API
3. `get_order_books()` - Fetch bid/ask books for UP and DOWN tokens (parallel)
4. State machine processes based on `window_state['state']`:
   - QUOTING: `check_and_place_arb()` looks for divergence opportunities
   - PAIRING_MODE: `run_pairing_mode()` completes hedge with escalating tolerance
   - DONE: Wait for window to close
5. `log_state()` - Display status, buffer tick data
6. `sheets_log_event()` - Log significant events to Google Sheets

**Order Placement Flow:**

1. `place_and_verify_order()` - Check for duplicates, place order, verify exists
2. Wait for fill via `get_order_status()` polling
3. `wait_and_sync_position()` - Wait settlement delay, verify position from API
4. `get_verified_fills()` - Dual-source verification (order status + position API + local)

**State Management:**
- Per-window state stored in global `window_state` dict
- State persists only within a 15-minute window
- Resets via `reset_window_state(slug)` at window transition
- Session counters track profits/losses across windows

## Key Abstractions

**Window State:**
- Purpose: Track all position and order data for current 15-min window
- Examples: `window_state['filled_up_shares']`, `window_state['state']`
- Pattern: Global dict, reset at window boundaries, no persistence

**State Machine:**
- Purpose: Control trading behavior based on position state
- States: `STATE_QUOTING`, `STATE_PAIRING`, `STATE_DONE`
- Pattern: Simple string constants with if/elif transitions

**Price Calculation:**
- Purpose: Convert API prices to discrete ticks and validate
- Examples: `floor_to_tick()`, `calculate_hedge_price()`, `calculate_99c_confidence()`
- Pattern: Pure functions with explicit rounding to 1-cent ticks

**Order Management:**
- Purpose: Place, track, cancel, and verify orders
- Examples: `place_limit_order()`, `cancel_all_orders()`, `get_order_status()`
- Pattern: Wrapper functions around py-clob-client with failsafe checks

## Entry Points

**Main Bot:**
- Location: `trading_bot_smart.py` - `main()` function at line 2147
- Triggers: Direct execution (`python3 trading_bot_smart.py`)
- Responsibilities: Initialize client, start main loop, handle window transitions

**Auto-Redeem Standalone:**
- Location: `auto_redeem.py` - `run_loop()` or `test_redeem_detection()`
- Triggers: `python auto_redeem.py` or `python auto_redeem.py --test`
- Responsibilities: Monitor for resolved positions, auto-claim winnings

**Imbalance Tracker Standalone:**
- Location: `imbalance_tracker.py` - `main()` function at line 246
- Triggers: `python3 imbalance_tracker.py`
- Responsibilities: Collect imbalance data, generate correlation reports

**Sheets Logger Test:**
- Location: `sheets_logger.py` - `test_logger()` function at line 453
- Triggers: `python3 sheets_logger.py`
- Responsibilities: Verify Google Sheets connectivity and logging

## Error Handling

**Strategy:** Defensive with graceful degradation

**Patterns:**
- Try/except blocks around all API calls with fallback behavior
- Retry with exponential backoff for Google Sheets operations (3 attempts)
- Multiple RPC endpoint fallbacks for Chainlink price feed
- `error_count` global tracks consecutive errors per window
- Optional module imports with `*_AVAILABLE` flags for graceful degradation
- Failsafe price/size limits to prevent catastrophic orders

**Critical Safety:**
- `FAILSAFE_MAX_BUY_PRICE = 0.85` - Never buy above 85c (except 99c capture)
- `FAILSAFE_MAX_SHARES = 50` - Never place order > 50 shares
- `FAILSAFE_MAX_ORDER_COST = $10.00` - Hard limit on order value
- `BAIL_TIME_REMAINING = 90s` - Force exit if unhedged at T-90s

## Cross-Cutting Concerns

**Logging:**
- Console output via custom `TeeLogger` class (stdout + file)
- Log file: `~/polybot/bot.log`
- Google Sheets: Events, Ticks (per-second), Windows (per-window summary)
- Activity log: `~/activity_log.jsonl` (JSONL format)

**Validation:**
- Failsafe checks in `place_limit_order()` before any order
- Position verification via `get_verified_position()` with retries
- Dual-source verification combining order status + position API + local state

**Authentication:**
- Polymarket: Private key from `~/.env` via py-clob-client
- Google Sheets: Service account JSON at `~/.google_sheets_credentials.json`
- Telegram: Bot token/chat ID from `~/.telegram-bot.json`

**Notifications:**
- Telegram alerts for: PROFIT_PAIR, LOSS_AVOID_PAIR, HARD_FLATTEN
- Functions: `notify_profit_pair()`, `notify_loss_avoid_pair()`, `notify_hard_flatten()`

---

*Architecture analysis: 2026-01-19*
