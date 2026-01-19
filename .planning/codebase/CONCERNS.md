# Codebase Concerns

**Analysis Date:** 2026-01-19

## Tech Debt

**Silent Exception Swallowing:**
- Issue: Multiple bare `except: pass` blocks hide errors and make debugging difficult
- Files: `trading_bot_smart.py` (lines 343, 460, 633, 664, 872, 2141, 2382, 2452), `imbalance_tracker.py` (lines 45, 69), `auto_redeem.py` (lines 169, 183, 220, 436)
- Impact: Errors go unnoticed, causing silent failures that are hard to diagnose
- Fix approach: Add proper exception handling with logging; at minimum use `except Exception as e:` and log the error

**Empty Return Patterns:**
- Issue: Functions return empty collections (`[]`, `{}`) on failure without indicating error state
- Files: `auto_redeem.py` (lines 169, 183, 220, 436), `trading_bot_smart.py` (line 633)
- Impact: Callers cannot distinguish between "no results" and "error occurred"
- Fix approach: Return `None` for errors vs empty collections for valid empty results, or use result types

**Hardcoded Configuration Values:**
- Issue: Many magic numbers and configuration values scattered throughout main bot file
- Files: `trading_bot_smart.py` (lines 140-290) - over 60 configuration constants
- Impact: Difficult to tune parameters without editing code; no environment-specific configs
- Fix approach: Extract to config file (YAML/JSON) with environment overrides

**Global Mutable State:**
- Issue: Heavy reliance on global variables (`window_state`, `clob_client`, `telegram_config`, etc.)
- Files: `trading_bot_smart.py` (lines 299-323), `sheets_logger.py` (line 387), `chainlink_feed.py` (line 136)
- Impact: Functions have hidden dependencies; testing is difficult; race conditions possible
- Fix approach: Use dependency injection or class-based design with explicit state management

**Mixed Responsibility in Main Bot:**
- Issue: `trading_bot_smart.py` is 2469 lines handling trading logic, position tracking, notifications, logging, and state management
- Files: `trading_bot_smart.py`
- Impact: Difficult to maintain, test, or modify individual components
- Fix approach: Extract into separate modules: `position_tracker.py`, `notifications.py`, `trading_strategies.py`

## Known Bugs

**Order Book Imbalance Signals Unreliable:**
- Symptoms: Signal predictions show ~50% accuracy (no better than random)
- Files: `orderbook_analyzer.py`, documented in CLAUDE.md line 333-335
- Trigger: Signals are generated but have 0% correlation with outcomes
- Workaround: `USE_ORDERBOOK_SIGNALS = True` but only for logging, not trading decisions

**99c Capture Strategy at 95% Confidence Can Lose:**
- Symptoms: Trade lost when market reversed in final 60 seconds
- Files: `trading_bot_smart.py` (lines 267-281)
- Trigger: 95% confidence trade at 98c ask price with T-69s reversed before close
- Workaround: Consider raising `CAPTURE_99C_MIN_CONFIDENCE` to 0.98

**Stale API Cache Issues:**
- Symptoms: Position API returns 0/0 when fills have occurred
- Files: `trading_bot_smart.py` (lines 887-929, 931-970)
- Trigger: API caching causes position data to lag behind actual fills
- Workaround: Implemented `get_verified_fills()` with dual-source verification and `max()` across sources

## Security Considerations

**Private Key in Environment Variable:**
- Risk: Private key loaded from `~/.env` file on server
- Files: `trading_bot_smart.py` (line 85), `auto_redeem.py` (line 30)
- Current mitigation: File stored outside project directory at `~/.env`
- Recommendations: Consider hardware wallet integration or vault service for production; ensure `~/.env` has restrictive permissions (600)

**Telegram Bot Token Exposure:**
- Risk: Telegram credentials stored in plaintext JSON
- Files: `trading_bot_smart.py` (line 434)
- Current mitigation: File at `~/.telegram-bot.json` outside project
- Recommendations: Ensure file permissions are restrictive; consider encrypted storage

**Google Sheets Service Account Key:**
- Risk: Service account JSON key stored on server filesystem
- Files: `sheets_logger.py` (line 35)
- Current mitigation: Stored at `~/.google_sheets_credentials.json`
- Recommendations: Ensure file permissions; rotate keys periodically; use minimal-scope service account

**No Rate Limiting on External API Calls:**
- Risk: Bot could be rate-limited or banned by external services
- Files: `trading_bot_smart.py` (multiple locations calling Polymarket, Coinbase APIs)
- Current mitigation: None explicit
- Recommendations: Add exponential backoff; implement request rate limiting

## Performance Bottlenecks

**Synchronous API Calls in Main Loop:**
- Problem: Position verification and order status checks block the main loop
- Files: `trading_bot_smart.py` (lines 887-929 `verify_position_from_api()`, 857-873 `get_order_status()`)
- Cause: HTTP requests with up to 5s timeout in synchronous code
- Improvement path: Use async/await or move to background threads

**Google Sheets Batch Flush:**
- Problem: Tick data buffered for 30 seconds before flush
- Files: `sheets_logger.py` (line 88 `TICK_FLUSH_INTERVAL = 30`)
- Cause: To avoid API rate limits, data is batched
- Improvement path: Current approach is reasonable; consider increasing batch size if sheets API allows

**Order Book Fetching Parallelism:**
- Problem: Two order books fetched with ThreadPoolExecutor but still adds latency
- Files: `trading_bot_smart.py` (lines 635-650)
- Cause: Sequential waits for futures with 3s timeout each
- Improvement path: Consider websocket subscriptions for real-time order book updates

## Fragile Areas

**PAIRING_MODE State Machine:**
- Files: `trading_bot_smart.py` (lines 1798-2131 `run_pairing_mode()`)
- Why fragile: Complex state with multiple exit conditions, escalation timers, bail logic, and hedge calculations
- Safe modification: Any changes require careful testing of all edge cases (timeout, reversal, bail vs hedge)
- Test coverage: No automated tests; manual testing only

**99c Capture Hedge Protection:**
- Files: `trading_bot_smart.py` (lines 1307-1382 `check_99c_capture_hedge()`)
- Why fragile: Interacts with position tracking, must coordinate with PAIRING_MODE
- Safe modification: Test with scenarios: confidence drop, partial fills, hedge fill failures
- Test coverage: None

**Position Tracking Synchronization:**
- Files: `trading_bot_smart.py` (lines 921-990 various sync functions)
- Why fragile: Multiple sources of truth (local state, API, order status); uses `max()` to reconcile
- Safe modification: Any changes to fill tracking must preserve the invariant that fills only increase
- Test coverage: None

## Scaling Limits

**Google Sheets Row Limit:**
- Current capacity: 50,000 rows in Ticks sheet
- Limit: Google Sheets has 10 million cell limit per spreadsheet
- Scaling path: Rotate to new sheets periodically; archive old data; consider time-series database for long-term storage

**Single-Threaded Bot:**
- Current capacity: One market window at a time
- Limit: Cannot trade multiple markets or windows simultaneously
- Scaling path: Refactor to async/event-driven architecture; separate market handlers

**Memory Accumulation:**
- Current capacity: `trades_log` list grows unbounded during session
- Limit: Long-running sessions could accumulate significant memory
- Scaling path: Periodically flush and clear `trades_log`; implement rotation

## Dependencies at Risk

**py-clob-client (Polymarket SDK):**
- Risk: Single-source dependency for all trading operations; no fallback
- Impact: If Polymarket changes API or deprecates SDK, bot is unusable
- Migration plan: Monitor Polymarket announcements; keep SDK version pinned; document raw API endpoints as backup

**Free RPC Endpoints (Chainlink):**
- Risk: Public endpoints may become rate-limited or unavailable
- Files: `chainlink_feed.py` (lines 42-47 `FREE_RPC_ENDPOINTS`)
- Impact: Chainlink price feed fails, falls back to Coinbase
- Migration plan: Consider paid RPC service (Alchemy, Infura) for reliability

**gspread Library:**
- Risk: Google API changes could break sheets integration
- Impact: Logging fails silently if gspread breaks
- Migration plan: Graceful degradation already implemented; consider local file logging as backup

## Missing Critical Features

**No Automated Testing:**
- Problem: Zero unit tests or integration tests for any component
- Blocks: Safe refactoring, confident deployments, regression prevention
- Priority: HIGH

**No Position Reconciliation:**
- Problem: No way to verify local position state matches on-chain reality at startup
- Files: Startup sync exists (`trading_bot_smart.py` lines 2286-2319) but uses API which can be stale
- Blocks: Recovery from crashes; detecting blockchain-level issues

**No Circuit Breaker:**
- Problem: No automatic shutdown if loss threshold exceeded or error rate too high
- Blocks: Unattended operation; limiting damage from bugs or market anomalies

**No Dry Run Mode:**
- Problem: Cannot test trading logic without real orders
- Blocks: Safe testing of new strategies; onboarding new developers

## Test Coverage Gaps

**Trading Logic (100% Untested):**
- What's not tested: `check_and_place_arb()`, `run_pairing_mode()`, `execute_99c_capture()`
- Files: `trading_bot_smart.py` (lines 1389-1736, 1798-2131)
- Risk: Bugs in core trading logic could cause financial losses
- Priority: HIGH

**Position Tracking (100% Untested):**
- What's not tested: `get_verified_fills()`, `verify_position_from_api()`, fill reconciliation
- Files: `trading_bot_smart.py` (lines 887-990)
- Risk: Position state could diverge from reality, causing wrong trades
- Priority: HIGH

**Notification Delivery (100% Untested):**
- What's not tested: Telegram notifications, Google Sheets logging
- Files: `trading_bot_smart.py` (lines 449-516), `sheets_logger.py`
- Risk: Failures go unnoticed if notifications fail silently
- Priority: MEDIUM

**Order Placement (100% Untested):**
- What's not tested: `place_limit_order()`, `place_and_verify_order()`, failsafe checks
- Files: `trading_bot_smart.py` (lines 802-1053)
- Risk: Orders could be placed incorrectly; failsafes might not trigger
- Priority: HIGH

**Auto-Redeem (100% Untested):**
- What's not tested: Position detection, redemption execution
- Files: `auto_redeem.py`
- Risk: Winning positions not redeemed; redemption could fail silently
- Priority: MEDIUM

---

*Concerns audit: 2026-01-19*
