# Phase 1: Tracking Infrastructure - Research

**Researched:** 2026-01-19
**Domain:** Python state tracking for trading bot
**Confidence:** HIGH

## Summary

This phase adds tracking infrastructure to support the danger score system. The bot already has established patterns for state management via `window_state` dictionary, constants defined in a dedicated section, and uses `collections.deque` for fixed-size rolling data structures.

The implementation requires:
1. Recording peak confidence when 99c capture fills (at fill detection time)
2. Maintaining a rolling 5-second BTC price window for velocity calculation
3. Adding `danger_score` field to `window_state` for logging

All three requirements fit cleanly into existing codebase patterns. No new dependencies needed.

**Primary recommendation:** Follow existing patterns - use `deque(maxlen=N)` for rolling window, add fields to `reset_window_state()`, define constants in the CONSTANTS section.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| collections.deque | stdlib | Fixed-size rolling window | Already used in codebase (line 39, 300) |
| time | stdlib | Timestamps for price history | Already used throughout |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| None | - | - | All requirements met with existing imports |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| deque | list with manual trimming | deque is cleaner, O(1) append/pop, already imported |
| timestamp tuples | dataclass | Overkill for 5-item window, tuples are simpler |

**Installation:**
No new packages needed - all requirements met with Python stdlib already imported.

## Architecture Patterns

### Current window_state Structure (lines 345-381)
The bot uses a dictionary initialized by `reset_window_state(slug)`:
```python
def reset_window_state(slug):
    """Initialize fresh state for a new window"""
    return {
        "window_id": slug,
        "market_id": None,
        "filled_up_shares": 0,
        "filled_down_shares": 0,
        # ... 30+ fields including 99c capture tracking
        "capture_99c_fill_notified": False,
        # ... more fields
    }
```

**Pattern to follow:** Add new fields to this function's return dict with sensible defaults.

### Constants Organization (lines 136-288)
Constants are organized in labeled sections:
```python
# ===========================================
# SECTION NAME IN ALL CAPS
# ===========================================
CONSTANT_NAME = value  # Comment explaining purpose
```

**Pattern to follow:** Add velocity window constant near other timing constants or create a new DANGER SCORE SETTINGS section.

### Rolling Window Pattern
The codebase already uses `deque` with `maxlen`:
```python
from collections import deque
api_latencies = deque(maxlen=10)
```

**Pattern to follow:** Use same approach for BTC price history:
```python
# In global state section:
btc_price_history = deque(maxlen=VELOCITY_WINDOW_SECONDS)

# In log_state() after getting BTC price:
if btc_price:
    btc_price_history.append((time.time(), btc_price))
```

### Where BTC Price is Fetched
BTC price is fetched in `log_state()` (lines 756-762):
```python
btc_price = None
if CHAINLINK_AVAILABLE and chainlink_feed:
    btc_price, btc_age = chainlink_feed.get_price_with_age()
    if btc_price:
        btc_str = f"BTC:${btc_price:,.0f}({btc_age}s) | "
```

`log_state()` is called once per second (throttled at line 713-715).

**Pattern to follow:** Append to rolling window in same location where `btc_price` is fetched.

### Where 99c Capture Fill is Detected
Fill detection happens in main loop (lines 2359-2374):
```python
if window_state.get('capture_99c_order') and not window_state.get('capture_99c_fill_notified'):
    order_id = window_state['capture_99c_order']
    status = get_order_status(order_id)
    if status.get('filled', 0) > 0:
        filled = status['filled']
        side = window_state['capture_99c_side']
        # ... celebration message ...
        window_state['capture_99c_fill_notified'] = True
```

**Pattern to follow:** At moment of fill detection (when setting `capture_99c_fill_notified = True`), also record the confidence value.

### Where Confidence is Calculated
Confidence is calculated in `check_99c_capture_opportunity()` (lines 1232-1260):
```python
conf_up, penalty_up = calculate_99c_confidence(ask_up, ttc)
if conf_up >= CAPTURE_99C_MIN_CONFIDENCE:
    return {'side': 'UP', 'ask': ask_up, 'confidence': conf_up, 'penalty': penalty_up}
```

At fill detection time, we need to recalculate current confidence (not reuse the order-placement confidence) because market may have moved.

### Recommended Project Structure
No new files needed. Changes isolated to `trading_bot_smart.py`:
```
trading_bot_smart.py
  ├── CONSTANTS section (lines 136-288)
  │     └── Add: VELOCITY_WINDOW_SECONDS = 5
  │
  ├── GLOBAL STATE section (lines 298-320)
  │     └── Add: btc_price_history = deque(maxlen=VELOCITY_WINDOW_SECONDS)
  │
  ├── reset_window_state() (lines 345-381)
  │     └── Add: "danger_score": 0, "capture_99c_peak_confidence": 0
  │
  ├── log_state() (lines 705-792)
  │     └── Add: btc_price_history.append(...) after btc_price fetch
  │
  └── Main loop fill detection (lines 2359-2374)
        └── Add: Capture peak confidence at fill time
```

### Anti-Patterns to Avoid
- **Global deque reset in wrong place:** Don't reset `btc_price_history` in `reset_window_state()` - it should be global and persist across windows
- **Recording confidence at order placement:** Record at fill time, not order placement - market may move between order and fill
- **Using list instead of deque:** Would need manual length management, error-prone

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fixed-size rolling window | Manual list trimming | `deque(maxlen=N)` | O(1), already imported, battle-tested |
| Time-based cleanup | Background thread cleaning old entries | `deque(maxlen=N)` + 1/sec updates | Simpler, main loop already runs every second |

**Key insight:** The bot already runs a once-per-second tick, so time-based windows naturally align with count-based windows. A 5-second window = 5 entries.

## Common Pitfalls

### Pitfall 1: Deque Reset Location
**What goes wrong:** Putting `btc_price_history = deque(...)` inside `reset_window_state()` would create a new deque each window, losing price history
**Why it happens:** Treating it like other window-specific state
**How to avoid:** Initialize once at module level (global state section)
**Warning signs:** Price velocity calculation shows 0 at window boundaries

### Pitfall 2: Wrong Confidence Timing
**What goes wrong:** Recording confidence at order placement instead of fill detection
**Why it happens:** Natural to record when placing order
**How to avoid:** Explicitly recalculate confidence at fill time using current ask price and TTL
**Warning signs:** Peak confidence doesn't match actual market state at fill

### Pitfall 3: Missing Price Updates
**What goes wrong:** BTC price not fetched or deque not updated when `btc_price` is None
**Why it happens:** Chainlink can occasionally fail
**How to avoid:** Only append when `btc_price` is not None (already the pattern for logging)
**Warning signs:** Gaps in velocity data, velocity returns None unexpectedly

### Pitfall 4: Deque Maxlen Mismatch
**What goes wrong:** Setting maxlen to wrong value (e.g., seconds * 1000 for milliseconds)
**Why it happens:** Confusion about update frequency
**How to avoid:** Remember: `log_state()` runs once per second, so 5 seconds = 5 entries
**Warning signs:** Window too large, uses excess memory

## Code Examples

### Pattern 1: Adding Constants
```python
# Source: Follows existing pattern from lines 136-288

# ===========================================
# DANGER SCORE SETTINGS
# ===========================================
VELOCITY_WINDOW_SECONDS = 5  # Rolling window for BTC price velocity
```

### Pattern 2: Adding Global State
```python
# Source: Follows existing pattern from lines 298-320

# Global rolling window for BTC price velocity
btc_price_history = deque(maxlen=VELOCITY_WINDOW_SECONDS)
```

### Pattern 3: Adding to window_state
```python
# Source: Follows existing pattern from lines 345-381

def reset_window_state(slug):
    return {
        # ... existing fields ...
        "danger_score": 0,                    # Current danger score (0-100)
        "capture_99c_peak_confidence": 0,     # Confidence at 99c fill time
    }
```

### Pattern 4: Updating Rolling Window
```python
# Source: Location is log_state() after line 762

# After btc_price is fetched:
if btc_price:
    btc_price_history.append((time.time(), btc_price))
```

### Pattern 5: Recording Peak Confidence at Fill
```python
# Source: Location is main loop lines 2359-2374

if status.get('filled', 0) > 0:
    filled = status['filled']
    side = window_state['capture_99c_side']

    # Record peak confidence at fill time
    ask_price = ...  # Get current ask for bet side
    remaining_secs = ...  # Current TTL
    confidence, _ = calculate_99c_confidence(ask_price, remaining_secs)
    window_state['capture_99c_peak_confidence'] = confidence

    window_state['capture_99c_fill_notified'] = True
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| N/A - new feature | deque for rolling window | Current | Simple, efficient |

**Deprecated/outdated:** N/A - using standard Python patterns

## Open Questions

1. **Exactly when to record confidence?**
   - At fill detection (recommended) or at fill confirmation?
   - Recommendation: At detection, which is when `capture_99c_fill_notified` is set to True

2. **What to do if price history is incomplete?**
   - At startup, deque will have < 5 entries for first few seconds
   - Recommendation: Velocity calculation should handle this gracefully (return 0 or None when < 2 entries)

3. **Should danger_score be logged to Google Sheets?**
   - Current tick logging includes prices, positions, imbalance
   - Recommendation: Yes, add to `buffer_tick()` in Phase 2 or later when actually calculating

## Sources

### Primary (HIGH confidence)
- `/Users/luislluis/MarkWatney/trading_bot_smart.py` - Direct code analysis
  - Lines 39, 300: Existing deque usage
  - Lines 136-288: Constants organization
  - Lines 345-381: window_state structure
  - Lines 705-792: log_state() and BTC price fetching
  - Lines 1212-1260: Confidence calculation
  - Lines 2359-2374: Fill detection
- Python stdlib documentation - deque behavior and complexity

### Secondary (MEDIUM confidence)
- None needed - all patterns verified directly in codebase

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - stdlib only, already imported
- Architecture: HIGH - follows existing codebase patterns exactly
- Pitfalls: HIGH - derived from direct code analysis

**Research date:** 2026-01-19
**Valid until:** 60 days (stable Python patterns, no external dependencies)
