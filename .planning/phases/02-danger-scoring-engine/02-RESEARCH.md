# Phase 2: Danger Scoring Engine - Research

**Researched:** 2026-01-19
**Domain:** Multi-signal danger scoring for trading bot
**Confidence:** HIGH

## Summary

Phase 2 implements the core danger scoring formula that combines 5 weighted signals into a single danger score (0.0-1.0). The infrastructure from Phase 1 provides: `btc_price_history` deque for velocity, `capture_99c_peak_confidence` for confidence drop, and `danger_score` field in `window_state`.

The implementation requires:
1. Define 6 configuration constants (threshold + 5 weights)
2. Create `calculate_danger_score()` function near other 99c capture functions
3. Call it from the main loop where `check_99c_capture_hedge()` is currently called
4. Store result in `window_state['danger_score']`

All data sources are already available in the codebase. No new dependencies needed. The existing `check_99c_capture_hedge()` function provides the pattern for accessing current ask prices and order book data.

**Primary recommendation:** Create a pure function `calculate_danger_score()` that takes all needed parameters and returns a dict with the score and individual signal components for logging.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| None | - | All stdlib | Pure Python math operations |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| collections.deque | stdlib | Price history access | Already available from Phase 1 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Single float return | Dict with components | Dict enables logging of all signals, worth the overhead |
| Inline calculation | Separate function | Function is cleaner, testable, reusable |

**Installation:**
No new packages needed - pure Python math.

## Architecture Patterns

### Recommended Function Placement
```
trading_bot_smart.py
  |
  +-- CONSTANTS section (lines 265-288)
  |     +-- CAPTURE_99C_* existing constants
  |     +-- ADD: DANGER_THRESHOLD = 0.40
  |     +-- ADD: DANGER_WEIGHT_CONFIDENCE = 3.0
  |     +-- ADD: DANGER_WEIGHT_IMBALANCE = 0.4
  |     +-- ADD: DANGER_WEIGHT_VELOCITY = 2.0
  |     +-- ADD: DANGER_WEIGHT_OPPONENT = 0.5
  |     +-- ADD: DANGER_WEIGHT_TIME = 0.3
  |
  +-- 99c capture functions (lines 1220-1390)
  |     +-- calculate_99c_confidence() (line 1220)
  |     +-- check_99c_capture_opportunity() (line 1240)
  |     +-- execute_99c_capture() (line 1271)
  |     +-- check_99c_capture_hedge() (line 1315)
  |     +-- ADD: calculate_danger_score() <-- NEW, place after check_99c_capture_hedge
  |
  +-- Main loop (line 2389-2391)
        +-- check_99c_capture_hedge() call
        +-- MODIFY: Call calculate_danger_score() here too
```

### Pattern 1: calculate_danger_score() Function Signature
**What:** Pure function that calculates danger from all inputs
**When to use:** Every tick while holding 99c position
**Example:**
```python
# Source: New function following codebase patterns
def calculate_danger_score(
    current_confidence: float,
    peak_confidence: float,
    our_imbalance: float,
    btc_price_history: deque,
    opponent_ask: float,
    time_remaining: float
) -> dict:
    """
    Calculate danger score for 99c capture position.

    Returns dict with 'score' (0.0-1.0) and individual signal components.
    """
```

### Pattern 2: Signal Component Calculation
**What:** Each signal normalized to contribute to 0-1 score range
**When to use:** Inside calculate_danger_score()
**Example:**
```python
# Confidence drop: (peak - current), positive when confidence falls
confidence_drop = peak_confidence - current_confidence
confidence_component = DANGER_WEIGHT_CONFIDENCE * confidence_drop

# Order book imbalance: Only counts if heavily against us (< -0.5)
imbalance_component = DANGER_WEIGHT_IMBALANCE * max(-our_imbalance - 0.5, 0)

# Price velocity: Negative = dropping (bad for UP position)
# Calculated from btc_price_history
velocity_component = DANGER_WEIGHT_VELOCITY * max(price_drop_5s, 0)

# Opponent strength: Only counts if opponent > 20c
opponent_component = DANGER_WEIGHT_OPPONENT * max(opponent_ask - 0.20, 0)

# Time decay: Ramps up in final 60 seconds (0 at 60s, 1.0 at 0s)
time_component = DANGER_WEIGHT_TIME * max(1 - time_remaining/60, 0)
```

### Pattern 3: Integration with Main Loop
**What:** Where to call the danger score calculation
**When to use:** Every tick after 99c capture fills but before hedge check
**Example:**
```python
# Location: Main loop, around line 2389-2391
# Currently:
#   if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged'):
#       check_99c_capture_hedge(books, remaining_secs)

# Modified to:
if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged'):
    # Calculate danger score
    danger_result = calculate_danger_score(
        current_confidence=...,
        peak_confidence=window_state.get('capture_99c_peak_confidence', 0),
        our_imbalance=...,  # From order book
        btc_price_history=btc_price_history,
        opponent_ask=...,  # From books
        time_remaining=remaining_secs
    )
    window_state['danger_score'] = danger_result['score']

    # Check if hedge needed (this will move to Phase 3)
    check_99c_capture_hedge(books, remaining_secs)
```

### Anti-Patterns to Avoid
- **Hardcoding weights:** All weights should be constants, not magic numbers in code
- **Modifying global state inside calculation:** Keep calculate_danger_score() pure - it returns a value, doesn't modify state
- **Forgetting None checks:** btc_price_history may have < 2 entries at startup; handle gracefully
- **Using raw imbalance value:** Need to use the imbalance for "our side" (the side we bet on)

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Price velocity calculation | Complex derivative estimation | Simple (newest - oldest) / time_diff | Matches spec, 5-second window is smoothing enough |
| Order book imbalance | Custom calculation | Use existing `orderbook_analyzer.analyze()` output | Already calculated every tick in log_state() |

**Key insight:** All signal data is already available - this phase is about combining them, not collecting them.

## Common Pitfalls

### Pitfall 1: Wrong "Our Side" Imbalance
**What goes wrong:** Using overall market imbalance instead of imbalance for the side we bet on
**Why it happens:** Order book analyzer returns both up_imbalance and down_imbalance
**How to avoid:** Check `window_state['capture_99c_side']` to determine which imbalance to use
**Warning signs:** Danger score increases when it shouldn't

The formula calls for "order book imbalance on our side" - if we bet UP, negative UP imbalance (sell pressure) is bad. If we bet DOWN, negative DOWN imbalance is bad.

### Pitfall 2: Price Velocity Direction
**What goes wrong:** Treating rising BTC price as dangerous when holding DOWN position
**Why it happens:** Not considering position direction in velocity calculation
**How to avoid:** If bet is UP, falling BTC is dangerous. If bet is DOWN, rising BTC is dangerous.
**Warning signs:** Hedge triggers when market is moving in our favor

Velocity should be normalized to "price moving against us":
- For UP position: `price_drop = (oldest_price - newest_price) / oldest_price` (positive when dropping)
- For DOWN position: `price_drop = (newest_price - oldest_price) / oldest_price` (positive when rising)

### Pitfall 3: Empty Price History
**What goes wrong:** Division by zero or index error when btc_price_history has < 2 entries
**Why it happens:** Startup condition, Chainlink failures
**How to avoid:** Check `len(btc_price_history) >= 2` before calculating velocity
**Warning signs:** Crash or NaN in danger score

### Pitfall 4: Time Decay Before Final 60s
**What goes wrong:** Time decay contributing when > 60s remaining
**Why it happens:** Not applying the `max(..., 0)` correctly
**How to avoid:** Formula is `max(1 - ttl/60, 0)` - this is 0 when ttl >= 60
**Warning signs:** Danger score has time component when plenty of time remains

### Pitfall 5: Unbounded Score
**What goes wrong:** Danger score can theoretically exceed 1.0 with extreme inputs
**Why it happens:** Weighted sum without cap
**How to avoid:** Either cap at 1.0 with `min(score, 1.0)` or accept that >1.0 is valid (means "very dangerous")
**Warning signs:** Score values > 1.0 in logs (may be acceptable)

## Code Examples

### Calculating Current Confidence
```python
# Source: Existing pattern from check_99c_capture_hedge() line 1347
# Confidence uses same formula from original entry check
bet_side = window_state.get('capture_99c_side')
if bet_side == "UP":
    current_ask = float(books['up_asks'][0]['price']) if books.get('up_asks') else 0
else:
    current_ask = float(books['down_asks'][0]['price']) if books.get('down_asks') else 0

current_confidence, time_penalty = calculate_99c_confidence(current_ask, ttc)
```

### Getting Order Book Imbalance for "Our Side"
```python
# Source: Existing pattern from log_state() lines 776-782
# Order book analyzer already runs every tick
ob_result = orderbook_analyzer.analyze(
    books.get('up_bids', []), books.get('up_asks', []),
    books.get('down_bids', []), books.get('down_asks', [])
)
up_imb = ob_result['up_imbalance']
down_imb = ob_result['down_imbalance']

# Select imbalance for our side
bet_side = window_state.get('capture_99c_side')
our_imbalance = up_imb if bet_side == "UP" else down_imb
```

### Calculating Price Velocity from History
```python
# Source: New code using btc_price_history from Phase 1
def get_price_velocity(btc_price_history: deque, bet_side: str) -> float:
    """
    Calculate price velocity over the rolling window.
    Returns the fractional price change against our position (positive = bad).
    """
    if len(btc_price_history) < 2:
        return 0.0

    oldest_ts, oldest_price = btc_price_history[0]
    newest_ts, newest_price = btc_price_history[-1]

    if oldest_price == 0:
        return 0.0

    # Normalize to fractional change
    price_change = (newest_price - oldest_price) / oldest_price

    # Invert for UP position (falling price = positive danger)
    if bet_side == "UP":
        return -price_change  # Negative change = positive danger
    else:  # DOWN position
        return price_change   # Positive change = positive danger
```

### Getting Opponent Ask Price
```python
# Source: Existing pattern from check_99c_capture_hedge() lines 1332-1341
bet_side = window_state.get('capture_99c_side')
if bet_side == "UP":
    opponent_asks = books.get('down_asks', [])
else:
    opponent_asks = books.get('up_asks', [])

opponent_ask = float(opponent_asks[0]['price']) if opponent_asks else 0.50
```

### Complete calculate_danger_score() Implementation
```python
def calculate_danger_score(
    current_confidence: float,
    peak_confidence: float,
    our_imbalance: float,
    btc_price_history: deque,
    opponent_ask: float,
    time_remaining: float,
    bet_side: str
) -> dict:
    """
    Calculate danger score for 99c capture position.

    Formula:
    danger_score = (
        3.0 * (peak_confidence - current_confidence) +
        0.4 * max(-our_imbalance - 0.5, 0) +
        2.0 * max(price_velocity_against_us, 0) +
        0.5 * max(opponent_ask - 0.20, 0) +
        0.3 * max(1 - ttl/60, 0)
    )

    Returns dict with 'score' and individual components for logging.
    """
    # Signal 1: Confidence drop from peak
    confidence_drop = max(peak_confidence - current_confidence, 0)
    conf_component = DANGER_WEIGHT_CONFIDENCE * confidence_drop

    # Signal 2: Order book imbalance against us
    # Negative imbalance = selling pressure. Only count if < -0.5
    imb_signal = max(-our_imbalance - 0.5, 0)
    imb_component = DANGER_WEIGHT_IMBALANCE * imb_signal

    # Signal 3: Price velocity against our position
    velocity = get_price_velocity(btc_price_history, bet_side)
    velocity_component = DANGER_WEIGHT_VELOCITY * max(velocity, 0)

    # Signal 4: Opponent ask strength
    opp_signal = max(opponent_ask - 0.20, 0)
    opp_component = DANGER_WEIGHT_OPPONENT * opp_signal

    # Signal 5: Time decay in final 60 seconds
    time_signal = max(1 - time_remaining / 60, 0) if time_remaining < 60 else 0
    time_component = DANGER_WEIGHT_TIME * time_signal

    # Total danger score
    total = conf_component + imb_component + velocity_component + opp_component + time_component

    return {
        'score': total,
        'confidence_drop': confidence_drop,
        'confidence_component': conf_component,
        'imbalance': our_imbalance,
        'imbalance_component': imb_component,
        'velocity': velocity,
        'velocity_component': velocity_component,
        'opponent_ask': opponent_ask,
        'opponent_component': opp_component,
        'time_remaining': time_remaining,
        'time_component': time_component,
    }
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single confidence threshold (85%) | Multi-signal weighted score | This phase | More nuanced, earlier detection |

**Deprecated/outdated:**
- `CAPTURE_99C_HEDGE_THRESHOLD = 0.85` - Will be replaced by `DANGER_THRESHOLD = 0.40` in Phase 3

## Data Flow Diagram

```
Main Loop (every second when holding 99c position)
    |
    +-- log_state() runs first
    |     +-- Fetches BTC price -> appends to btc_price_history
    |     +-- Calls orderbook_analyzer.analyze() -> up_imb, down_imb
    |
    +-- check for 99c fill notification
    |     (captures peak_confidence at fill time)
    |
    +-- calculate_danger_score()  <-- NEW
    |     Inputs:
    |       - current_confidence (from calculate_99c_confidence)
    |       - peak_confidence (from window_state)
    |       - our_imbalance (from orderbook_analyzer)
    |       - btc_price_history (global deque)
    |       - opponent_ask (from books)
    |       - time_remaining
    |       - bet_side
    |     Output:
    |       - danger_score dict stored in window_state
    |
    +-- check_99c_capture_hedge()  <-- Phase 3 will modify this
          (currently uses confidence threshold, will use danger score)
```

## Open Questions

1. **Should danger score be capped at 1.0?**
   - With extreme inputs, score can exceed 1.0
   - Recommendation: Don't cap - values > 1.0 mean "very dangerous" which is useful info
   - Can always add `min(score, 1.0)` later if needed for UI/logging

2. **What if order book analyzer is unavailable?**
   - `ORDERBOOK_ANALYZER_AVAILABLE` could be False
   - Recommendation: Default imbalance to 0 (neutral), score still works with other 4 signals

3. **What if Chainlink is unavailable?**
   - `btc_price_history` would be empty
   - Recommendation: Default velocity to 0 (neutral), already handled in velocity function

## Sources

### Primary (HIGH confidence)
- `/Users/luislluis/MarkWatney/trading_bot_smart.py` - Direct code analysis
  - Lines 1220-1238: calculate_99c_confidence() function
  - Lines 1315-1391: check_99c_capture_hedge() function (existing hedge logic)
  - Lines 776-789: Order book imbalance calculation in log_state()
  - Lines 304-305: btc_price_history deque setup
  - Lines 2389-2391: Where hedge check is called in main loop
- `/Users/luislluis/MarkWatney/orderbook_analyzer.py` - OrderBookAnalyzer.analyze() returns imbalance
- `/Users/luislluis/MarkWatney/.planning/REQUIREMENTS.md` - Formula and weight specifications

### Secondary (MEDIUM confidence)
- Phase 1 RESEARCH.md - Infrastructure patterns established

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pure Python math, no dependencies
- Architecture: HIGH - follows existing codebase patterns exactly
- Pitfalls: HIGH - derived from direct code analysis and formula examination

**Research date:** 2026-01-19
**Valid until:** 60 days (stable Python patterns, no external dependencies)
