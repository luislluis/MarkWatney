# Polybot Version Registry

## Current Version: v1.12 "Resilient Eagle"

| Version | DateTime | Codename | Changes | Status |
|---------|----------|----------|---------|--------|
| v1.12 | 2026-01-21 PST | Resilient Eagle | 99c capture recovery from 403 errors via position polling + retry logic | Active |
| v1.11 | 2026-01-21 PST | Precise Falcon | Fix: Record fill prices for second leg in pairing mode + log 99c capture bid_price | Archived |
| v1.10 | 2026-01-20 PST | Swift Hare | 5-second rule: if second ARB leg doesn't fill in 5s, bail immediately | Archived |
| v1.9 | 2026-01-20 PST | Nimble Otter | OB-based early bail: detect ARB reversals via order book before price moves (target <10c loss) | Archived |
| v1.8 | 2026-01-19 20:30 PST | Cautious Crow | 99c capture: skip entry when ask >= 99c (avoids reversal traps) | Archived |
| v1.7 | 2026-01-19 14:00 PST | Watchful Owl | Observability: danger score in Ticks, signal breakdown in hedge events | Archived |
| v1.6 | 2026-01-19 13:30 PST | Swift Panther | Hedge execution: replace confidence trigger with danger score >= 0.40 | Archived |
| v1.5 | 2026-01-19 13:00 PST | Keen Falcon | Danger scoring engine: 5-signal weighted scoring system | Archived |
| v1.4 | 2026-01-19 12:30 PST | Steady Hawk | Tracking infrastructure: peak confidence, price velocity | Archived |
| v1.3 | 2026-01-16 01:00 PST | Neon Falcon | Fix: Cancel race condition - track pending hedge order IDs | Archived |
| v1.2 | 2026-01-16 00:30 PST | Silent Thunder | Fix: PAIRING_MODE race condition causing duplicate orders | Archived |
| v1.1 | 2026-01-15 21:58 PST | Quantum Badger | Auto-redeem: direct CTF contract redemption through Gnosis Safe | Archived |
| v1.0 | 2026-01-15 20:50 PST | Iron Phoenix | Baseline - includes PAIRING_MODE hedge escalation + 99c capture hedge protection | Archived |

## Version History Details

### v1.12 - Resilient Eagle (2026-01-21)
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

### v1.11 - Precise Falcon (2026-01-21)
- **Dashboard accuracy fix: Fill price recording**
- Bug 1: CAPTURE_99C events logged ask_price (market price) instead of bid_price (order price of 99c)
  - Fixed by adding `bid_price=CAPTURE_99C_BID_PRICE` to CAPTURE_99C event logging
- Bug 2: Pairing mode fills didn't record second leg fill price
  - Added price recording in 4 locations where second leg fills:
    1. EARLY_HEDGE path - records hedge_fill_price when early hedge taken
    2. Regular hedge path - records fill price when hedge order fills
    3. Taker path - records fill price when taker order fills
    4. HEDGE_ALREADY_FILLED path - records price when previous order filled despite cancel
  - Prints `HEDGE_PRICE_RECORDED: <side> @ <price>c` or `TAKER_PRICE_RECORDED: <side> @ <price>c`
- These fixes ensure performance dashboard shows exact entry prices for all trades

### v1.10 - Swift Hare (2026-01-20)
- **5-second rule for ARB pairing**
- `PAIR_WINDOW_SECONDS = 5` - If second leg doesn't fill in 5 seconds, bail immediately
- Observation: Most successful ARB pairs complete within 5 seconds
- After 5 seconds without pairing, take best available bail price immediately
- Simplifies logic: no more waiting 30+ seconds hoping for better prices
- Market reversal / OB detection can still trigger earlier bail within the 5-second window

### v1.9 - Nimble Otter (2026-01-20)
- **OB-based early bail for ARB strategy**
- Detects reversals via order book imbalance before price moves significantly
- `OB_REVERSAL_THRESHOLD = -0.25` - Bail when filled side has 25%+ selling pressure
- `OB_REVERSAL_PRICE_CONFIRM = 0.03` - Only need 3c price drop when OB confirms
- Target: <10c loss instead of 23c (in the window 1768867800 example)
- Runs during first 15 seconds of PAIRING_MODE when reversals are most likely
- Combined with existing price-based reversal detection for redundancy

### v1.8 - Cautious Crow (2026-01-19)
- **99c capture reversal trap prevention**
- Added `CAPTURE_99C_MAX_ASK = 0.99` threshold
- If ask price >= 99c, skip the 99c capture entirely
- Rationale: When ask is at 99c+, our 99c bid is at or below ask
- A fill means price dropped TO our bid = catching a falling knife
- When ask < 99c, our bid is above ask = immediate fill, safe entry
- This would have prevented the -$1.60 loss in window 1768870800

### v1.7 - Watchful Owl (2026-01-19)
- **Full observability for danger score system**
- Danger score (D:X.XX) displayed in console output every tick
- DangerScore column added to Google Sheets Ticks
- Signal breakdown logged on hedge events (confidence, velocity, OB, opponent, time)

### v1.6 - Swift Panther (2026-01-19)
- **Danger score triggers hedge instead of simple confidence threshold**
- Replace 85% confidence check with danger_score >= 0.40
- More nuanced triggering using multiple signals

### v1.5 - Keen Falcon (2026-01-19)
- **Multi-signal danger scoring engine**
- 5 weighted signals: confidence drop (3.0), OB imbalance (0.4), velocity (2.0), opponent ask (0.5), time decay (0.3)
- Returns both raw values and weighted components

### v1.4 - Steady Hawk (2026-01-19)
- **Tracking infrastructure for danger scoring**
- Peak confidence tracking per 99c capture position
- BTC price velocity tracking (5-second rolling window)
- Foundation for v1.5 danger scoring

### v1.3 - Neon Falcon (2026-01-16)
- **Bug fix**: Cancel race condition causing duplicate hedge orders
- Track pending_hedge_order_id in window_state
- Before placing new hedge, check if previous order was filled (despite cancel)
- Prevents duplicates like 10 UP / 5 DOWN when hedging

### v1.2 - Silent Thunder (2026-01-16)
- **Bug fix**: Race condition in PAIRING_MODE causing duplicate orders
- Added position re-verification after cancel_all_orders()
- Prevents placing new order if original order already filled

### v1.1 - Quantum Badger (2026-01-15)
- **Auto-redeem feature** for winning positions
- Direct CTF contract redemption via Gnosis Safe execTransaction
- Detects resolved markets with winning positions
- Automatically claims USDC from winning outcome tokens
- Test mode: `test_redeem_detection()` for dry-run

### v1.0 - Iron Phoenix (2026-01-15)
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

## Codename Convention
Each version gets a unique two-word codename: `[Adjective] [Animal/Object]`
Examples: Iron Phoenix, Quantum Badger, Silent Thunder, Neon Falcon
