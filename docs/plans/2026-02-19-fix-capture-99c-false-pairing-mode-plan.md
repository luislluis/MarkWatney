---
title: "Fix: 99c capture triggers false PAIRING_MODE via premature fill tracking"
type: fix
date: 2026-02-19
severity: high
version: v1.50
codename: TBD
---

# Fix: 99c capture triggers false PAIRING_MODE via premature fill tracking

## Overview

The bot placed a 99c capture order for 9 DOWN shares at 95c. Immediately after placement, it falsely entered PAIRING_MODE (thinking it had an imbalance of 0/-9), called `cancel_all_orders()` which tried to cancel the order it just placed, then returned to IDLE. The order actually filled on Polymarket at ~50c during end-of-window liquidity death, but the bot never detected the fill. PnL and ROI were never updated.

## Root Cause

In `execute_99c_capture()` (server line 2188-2190), the code sets `capture_99c_filled_down = 9` **immediately on order placement**, not on fill confirmation:

```python
# Track captured shares to exclude from pairing logic
if side == 'UP':
    window_state['capture_99c_filled_up'] = shares    # <-- BUG: set before fill!
else:
    window_state['capture_99c_filled_down'] = shares   # <-- BUG: set before fill!
```

This causes `get_arb_imbalance()` to compute:
```
arb_down = filled_down_shares(0) - capture_99c_filled_down(9) = -9
arb_imbalance = arb_up(0) - arb_down(-9) = 9  →  triggers PAIRING_MODE
```

## Fixes

### Fix 1: Don't track capture shares until fill is confirmed

**File:** `trading_bot_smart.py` (server: `~/polymarket_bot/trading_bot_smart.py`)
**Location:** `execute_99c_capture()`, server lines 2186-2192

**Before:**
```python
if success:
    window_state['capture_99c_used'] = True
    window_state['capture_99c_order'] = order_id
    window_state['capture_99c_side'] = side
    window_state['capture_99c_shares'] = shares
    # Track captured shares to exclude from pairing logic
    if side == 'UP':
        window_state['capture_99c_filled_up'] = shares
    else:
        window_state['capture_99c_filled_down'] = shares
```

**After:**
```python
if success:
    window_state['capture_99c_used'] = True
    window_state['capture_99c_order'] = order_id
    window_state['capture_99c_side'] = side
    window_state['capture_99c_shares'] = shares
    # NOTE: Do NOT set capture_99c_filled_up/down here.
    # Fill tracking happens in the main loop fill detection (line ~3830).
    # Setting it here causes get_arb_imbalance() to return a phantom
    # negative value, triggering false PAIRING_MODE entry.
```

**Why:** The fill detection code at server line ~3830 already correctly sets `capture_99c_filled_{side}` after confirming fills via `get_order_status()`. The premature setting was the sole cause of the false imbalance.

### Fix 2: Skip imbalance check entirely when ARB is disabled

**File:** `trading_bot_smart.py`
**Location:** Imbalance detection block, server lines ~3947-3970

The imbalance/PAIRING_MODE logic only makes sense for ARB trading (two-sided positions). When `ARB_ENABLED = False` (sniper-only mode), there are no arb positions to become imbalanced. Guard the entire block:

**Before:**
```python
if abs(arb_imbalance) > MICRO_IMBALANCE_TOLERANCE:
    print(f"[{ts()}] Potential imbalance detected ({arb_up}/{arb_down}), verifying...")
    ...
    if abs(arb_imbalance) > MICRO_IMBALANCE_TOLERANCE:
        print(f"🔴 ARB IMBALANCE CONFIRMED! ...")
        ...
        cancel_all_orders()
        run_pairing_mode(books, remaining_secs)
```

**After:**
```python
if ARB_ENABLED and abs(arb_imbalance) > MICRO_IMBALANCE_TOLERANCE:
    print(f"[{ts()}] Potential imbalance detected ({arb_up}/{arb_down}), verifying...")
    ...
```

**Why:** This is a belt-and-suspenders fix. Even after Fix 1, the imbalance check is meaningless when ARB is off. Adding `ARB_ENABLED` as a guard prevents any future regression from triggering PAIRING_MODE in sniper-only mode.

### Fix 3: Fix display bug showing "99c" when bid is actually 95c

**File:** `trading_bot_smart.py`
**Location:** `execute_99c_capture()`, server line 2173

**Before:**
```python
print(f"│  Bidding {shares} shares @ 99c = ${shares * CAPTURE_99C_BID_PRICE:.2f}".ljust(44) + "│")
```

**After:**
```python
print(f"│  Bidding {shares} shares @ {CAPTURE_99C_BID_PRICE*100:.0f}c = ${shares * CAPTURE_99C_BID_PRICE:.2f}".ljust(44) + "│")
```

**Why:** Hardcoded "99c" is misleading when `CAPTURE_99C_BID_PRICE = 0.95`. The math ($8.55) was already correct but the label was wrong.

## Acceptance Criteria

- [x] `capture_99c_filled_up/down` is NOT set on order placement — only after confirmed fill
- [x] Imbalance detection block is gated behind `ARB_ENABLED`
- [x] Display shows actual bid price, not hardcoded "99c"
- [ ] Bot can place a 99c capture, have it fill, and correctly track the fill without entering PAIRING_MODE
- [x] Version bumped to v1.50 with descriptive codename

## Testing

1. Deploy to server
2. Watch for next CAPTURE_99C order placement
3. Verify: no "Potential imbalance detected" after placement
4. Verify: fill is detected and CAPTURE_FILL logged
5. Verify: PnL and ROI correctly reflect the trade

## Risk Assessment

**Low risk.** All three fixes are defensive:
- Fix 1 removes premature state mutation (the correct path already exists)
- Fix 2 adds a guard that makes an impossible path explicit
- Fix 3 is cosmetic

No new logic is introduced. The fill detection path (server line ~3800-3835) is unchanged and already handles fills correctly when it's allowed to run without PAIRING_MODE interference.
