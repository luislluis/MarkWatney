# Phase 3: Hedge Execution - Research

**Researched:** 2026-01-19
**Domain:** Hedge order execution for trading bot
**Confidence:** HIGH

## Summary

Phase 3 implements hedge execution that triggers when the danger score (from Phase 2) exceeds the 0.40 threshold. The hedge mechanism buys the opposite side at market price, locking in a small controlled loss instead of risking total loss of the 99c capture position.

The implementation requires:
1. Modify `check_99c_capture_hedge()` to use danger score instead of confidence threshold
2. Ensure hedge shares match original 99c capture fill shares
3. Respect the 50c max price limit for opposite side
4. Use existing `place_and_verify_order()` with retry/deduplication

All infrastructure is already in place from Phase 1 and 2. The existing `check_99c_capture_hedge()` function provides 90% of the needed logic - only the trigger condition changes from confidence-based to danger-score-based.

**Primary recommendation:** Replace the confidence threshold check (`new_confidence < CAPTURE_99C_HEDGE_THRESHOLD`) with danger score check (`danger_score >= DANGER_THRESHOLD`). The existing hedge execution logic (order placement, position tracking, logging) requires no changes.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| None | - | All stdlib | Pure Python logic, reuses existing order execution |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| py-clob-client | existing | Order execution | Used via existing `place_and_verify_order()` wrapper |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Buy opposite (arb) | Sell position | Selling has slippage risk; buying opposite guarantees locked loss |
| place_and_verify_order() | place_limit_order() direct | place_and_verify has deduplication and retry built-in |

**Installation:**
No new packages needed - uses existing bot infrastructure.

## Architecture Patterns

### Current vs Modified Function Flow
```
CURRENT check_99c_capture_hedge():
  Guards: HEDGE_ENABLED, fill_notified, not_hedged
  Calculate: new_confidence from current ask
  Trigger: new_confidence < CAPTURE_99C_HEDGE_THRESHOLD (85%)
  Execute: place_and_verify_order() on opposite side

MODIFIED check_99c_capture_hedge():
  Guards: HEDGE_ENABLED, fill_notified, not_hedged  <-- UNCHANGED
  Check: danger_score from window_state              <-- CHANGED
  Trigger: danger_score >= DANGER_THRESHOLD (0.40)   <-- CHANGED
  Execute: place_and_verify_order() on opposite side <-- UNCHANGED
```

### Pattern 1: Danger Score Trigger Condition
**What:** Replace confidence threshold with danger score threshold
**When to use:** In the trigger condition of check_99c_capture_hedge()
**Example:**
```python
# Source: Modified from existing check_99c_capture_hedge() line 1358
# OLD: if new_confidence < CAPTURE_99C_HEDGE_THRESHOLD:
# NEW:
danger_score = window_state.get('danger_score', 0)
if danger_score >= DANGER_THRESHOLD:
    # Execute hedge...
```

### Pattern 2: Shares Matching
**What:** Hedge shares must equal original 99c capture fill shares
**When to use:** When determining hedge order size
**Example:**
```python
# Source: Existing pattern from check_99c_capture_hedge() line 1360
# Already correct - uses capture_99c_shares from window_state
shares = window_state.get('capture_99c_shares', 0)
```

### Pattern 3: Price Limit Check
**What:** Don't hedge if opposite side too expensive (>= 50c)
**When to use:** Before placing hedge order
**Example:**
```python
# Source: Existing pattern from check_99c_capture_hedge() line 1362
# Already correct - checks opposite_ask < 0.50
if shares > 0 and opposite_ask < 0.50:
    # Place hedge order
```

### Pattern 4: Double-Hedge Prevention
**What:** Only hedge once per position
**When to use:** Guard at start of hedge check
**Example:**
```python
# Source: Existing pattern from check_99c_capture_hedge() line 1332
# Already correct - checks capture_99c_hedged flag
if window_state.get('capture_99c_hedged'):
    return  # Already hedged
```

### Anti-Patterns to Avoid
- **Removing confidence threshold without adding danger score:** Would break hedge trigger
- **Using different shares count:** Must use `capture_99c_shares`, not recalculate
- **Modifying hedge execution logic:** It's already correct, only trigger changes
- **Bypassing 50c limit:** This protects against expensive hedges

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Order placement | Custom CLOB API calls | `place_and_verify_order()` | Has retry, deduplication, verification |
| Order status checking | Direct API polling | `get_order_status()` | Already handles timeouts and errors |
| Position tracking | Manual calculation | Existing window_state tracking | Proven correct, handles edge cases |
| Double-hedge prevention | Custom flag management | Existing `capture_99c_hedged` flag | Already integrated |

**Key insight:** Phase 3 is almost entirely a "rewiring" task - changing the trigger condition from confidence-based to danger-score-based. The execution machinery exists and is battle-tested.

## Common Pitfalls

### Pitfall 1: Removing Old Threshold Check Without Adding New One
**What goes wrong:** Hedge never triggers or always triggers
**Why it happens:** Incomplete replacement of trigger condition
**How to avoid:** Replace `new_confidence < CAPTURE_99C_HEDGE_THRESHOLD` with `danger_score >= DANGER_THRESHOLD` - both must exist
**Warning signs:** Hedges don't match expected behavior based on danger score

### Pitfall 2: Using Wrong Shares Count
**What goes wrong:** Hedge creates unbalanced position
**Why it happens:** Calculating shares instead of using stored value
**How to avoid:** Always use `window_state.get('capture_99c_shares', 0)`
**Warning signs:** ARB imbalance detected after hedge

The formula requires: `hedge_shares == capture_99c_shares` (HEDGE-05)

### Pitfall 3: Hedging Above 50c
**What goes wrong:** Loss exceeds expected bounds
**Why it happens:** Not checking opposite_ask price before hedging
**How to avoid:** Keep existing check: `opposite_ask < 0.50`
**Warning signs:** Combined position cost exceeds $1.00 by more than expected

With 99c original + 50c hedge = $1.49 cost for $1.00 return = $0.49 loss
The 50c limit ensures max loss per share is $0.49 (actually lower since we typically hedge at 20-40c)

### Pitfall 4: Race Condition in Hedge Flag
**What goes wrong:** Multiple hedge orders placed
**Why it happens:** Hedge flag not set before order confirmation
**How to avoid:** Keep existing pattern where `capture_99c_hedged = True` is set after successful order
**Warning signs:** Multiple hedge orders for same position

Existing code is correct - it sets the flag only after `success` from order placement.

### Pitfall 5: Danger Score Not Available
**What goes wrong:** Hedge never triggers because score is 0
**Why it happens:** Main loop integration from Phase 2 not working
**How to avoid:** Verify `window_state['danger_score']` is being updated every tick
**Warning signs:** Danger score always 0 in logs

Dependency: Phase 2 must be complete and working.

## Code Examples

### Modified check_99c_capture_hedge() Trigger Condition
```python
# Source: Modified from check_99c_capture_hedge() lines 1354-1358
# Location: trading_bot_smart.py

# REMOVE these lines (old confidence-based trigger):
# new_confidence, time_penalty = calculate_99c_confidence(current_ask, ttc)
# if new_confidence < CAPTURE_99C_HEDGE_THRESHOLD:

# REPLACE with (new danger-score-based trigger):
danger_score = window_state.get('danger_score', 0)
if danger_score >= DANGER_THRESHOLD:
    opposite_ask = float(opposite_asks[0]['price'])
    shares = window_state.get('capture_99c_shares', 0)

    if shares > 0 and opposite_ask < 0.50:
        # ... existing hedge execution code unchanged ...
```

### Complete Modified Function (Key Changes Only)
```python
def check_99c_capture_hedge(books, ttc):
    """Monitor 99c capture position and hedge if danger score too high."""
    global window_state

    # Guards - UNCHANGED
    if not CAPTURE_99C_HEDGE_ENABLED:
        return
    if not window_state.get('capture_99c_fill_notified'):
        return  # Not filled yet
    if window_state.get('capture_99c_hedged'):
        return  # Already hedged - prevents double-hedging (HEDGE-03)

    bet_side = window_state.get('capture_99c_side')
    if not bet_side:
        return

    # Get opposite side info - UNCHANGED
    if bet_side == "UP":
        opposite_asks = books.get('down_asks', [])
        opposite_token = window_state['down_token']
        opposite_side = "DOWN"
    else:
        opposite_asks = books.get('up_asks', [])
        opposite_token = window_state['up_token']
        opposite_side = "UP"

    if not opposite_asks:
        return

    # CHANGED: Use danger score instead of confidence threshold
    danger_score = window_state.get('danger_score', 0)
    if danger_score >= DANGER_THRESHOLD:  # HEDGE-01: trigger at 0.40
        opposite_ask = float(opposite_asks[0]['price'])
        shares = window_state.get('capture_99c_shares', 0)  # HEDGE-05: match original shares

        if shares > 0 and opposite_ask < 0.50:  # HEDGE-04: respect 50c limit
            # Print banner
            print()
            print(f"┌─────────────── 99c HEDGE TRIGGERED ───────────────┐")
            print(f"│  Danger score: {danger_score:.2f} >= {DANGER_THRESHOLD:.2f} threshold".ljust(50) + "│")
            print(f"│  Bet: {bet_side} @ 99c".ljust(50) + "│")
            print(f"│  Hedging: {shares} {opposite_side} @ {opposite_ask*100:.0f}c".ljust(50) + "│")
            print(f"└───────────────────────────────────────────────────┘")

            # HEDGE-02: Buy opposite side at market (take the ask)
            # HEDGE-05: Uses existing place_and_verify_order with retries
            success, order_id, status = place_and_verify_order(opposite_token, opposite_ask, shares)
            if success:
                # ... rest unchanged ...
```

### Logging Hedge Events with Signal Breakdown
```python
# Source: Enhanced from existing sheets_log_event call at line 1392
# OPTIONAL ENHANCEMENT for LOG-02 (Phase 4)
sheets_log_event("99C_HEDGE", window_state.get('window_id', ''),
               bet_side=bet_side, hedge_side=opposite_side,
               hedge_price=opposite_ask, combined=combined, loss=total_loss,
               # NEW: signal breakdown
               danger_score=danger_score,
               conf_drop=danger_result.get('confidence_drop', 0),
               imbalance=danger_result.get('imbalance', 0),
               velocity=danger_result.get('velocity', 0),
               opponent=danger_result.get('opponent_ask', 0),
               time_left=danger_result.get('time_remaining', 0))
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single confidence threshold (85%) | Multi-signal danger score (0.40) | This phase | Earlier detection of reversals |
| CAPTURE_99C_HEDGE_THRESHOLD | DANGER_THRESHOLD | This phase | Threshold meaning changes (higher = more danger, not less confidence) |

**Deprecated/outdated:**
- `CAPTURE_99C_HEDGE_THRESHOLD = 0.85` - No longer used for hedge trigger after this phase
- `new_confidence < threshold` check in check_99c_capture_hedge() - Replaced with danger score check

Note: Keep `CAPTURE_99C_HEDGE_THRESHOLD` constant in code for now - may be useful for comparison logging or fallback.

## Verification Checklist

After implementation, verify:

| Requirement | Verification | Expected |
|-------------|-------------|----------|
| HEDGE-01 | `grep "danger_score >= DANGER_THRESHOLD" trading_bot_smart.py` | 1 match in check_99c_capture_hedge |
| HEDGE-02 | Existing code unchanged - uses `place_and_verify_order()` at ask price | Order placed at opposite ask |
| HEDGE-03 | Existing guard: `if window_state.get('capture_99c_hedged')` unchanged | Early return prevents double-hedge |
| HEDGE-04 | Existing check: `opposite_ask < 0.50` unchanged | Orders blocked above 50c |
| HEDGE-05 | Existing code: `shares = window_state.get('capture_99c_shares', 0)` unchanged | Shares match original |

## Open Questions

1. **Should we log the old confidence alongside danger score?**
   - Helpful for comparing old vs new trigger behavior
   - Recommendation: Yes, add to log output for debugging during transition

2. **Should CAPTURE_99C_HEDGE_THRESHOLD be removed?**
   - It's no longer used for trigger decision
   - Recommendation: Keep it for comparison logging, document as deprecated

3. **What if danger score never reaches 0.40?**
   - Position would remain unhedged until window close
   - This is expected behavior - only hedge when genuinely dangerous
   - The old 85% confidence threshold was more aggressive

4. **Should hedge execution log the danger_result breakdown?**
   - Would help post-mortem analysis of hedge decisions
   - Recommendation: Pass danger_result to sheets_log_event for LOG-02 (Phase 4)

## Sources

### Primary (HIGH confidence)
- `/Users/luislluis/MarkWatney/trading_bot_smart.py` - Direct code analysis
  - Lines 1323-1398: `check_99c_capture_hedge()` - existing function to modify
  - Lines 1033-1067: `place_and_verify_order()` - existing order execution
  - Lines 291-296: `DANGER_THRESHOLD` and `DANGER_WEIGHT_*` constants
  - Lines 2488-2528: Main loop danger score calculation (Phase 2 integration)
  - Lines 377-395: window_state fields for 99c capture tracking
- `/Users/luislluis/MarkWatney/.planning/REQUIREMENTS.md` - HEDGE-01 through HEDGE-05 specifications
- `/Users/luislluis/MarkWatney/.planning/phases/02-danger-scoring-engine/02-RESEARCH.md` - Danger scoring implementation details

### Secondary (MEDIUM confidence)
- `/Users/luislluis/MarkWatney/.planning/STATE.md` - Prior decisions context

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - uses existing, proven order execution
- Architecture: HIGH - minimal change to existing function
- Pitfalls: HIGH - derived from direct code analysis and existing patterns

**Research date:** 2026-01-19
**Valid until:** 60 days (stable Python patterns, no external dependencies)
