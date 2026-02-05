---
title: "fix: Track actual filled shares instead of requested amount for 99c captures"
type: fix
date: 2026-02-05
---

# fix: Track actual filled shares instead of requested amount for 99c captures

## Problem Statement

When the bot places a 99c capture order for 6 shares, Polymarket may only fill 5.99 shares (partial fill). The bot currently tracks the **requested** amount (6), not the **actual** filled amount (5.99). When the bot later tries to sell 6 shares via HARD_STOP, Polymarket rejects with:

```
PolyApiException[status_code=400, error_message={'error': 'not enough balance / allowance'}]
```

This caused a **$5.94 loss** on window `1770267600` when 6 shares got stuck and couldn't be sold.

## Root Cause

In `trading_bot_smart.py`:

**Line 2062-2064** (when order is PLACED):
```python
if side == 'UP':
    window_state['capture_99c_filled_up'] = shares  # Sets REQUESTED amount (e.g., 6)
else:
    window_state['capture_99c_filled_down'] = shares
```

**Line 3400-3401** (when fill is DETECTED):
```python
filled = status['filled']  # Gets ACTUAL amount (e.g., 5.99)
# BUT NEVER UPDATES capture_99c_filled_up/down with this value!
```

**Line 1301** (when HARD_STOP runs):
```python
shares = window_state.get(f'capture_99c_filled_{side.lower()}', 0)  # Uses stale REQUESTED amount
```

## Proposed Solution

Update `capture_99c_filled_up/down` with the **actual** filled amount when the fill is detected.

## Acceptance Criteria

- [ ] When 99c capture fill is detected, update `capture_99c_filled_up` or `capture_99c_filled_down` with actual `filled` value from order status
- [ ] HARD_STOP uses the actual filled amount, not requested amount
- [ ] Add safety: use `math.floor()` when selling to handle any remaining precision issues
- [ ] Log a warning if actual fill differs from requested by more than 0.01 shares

## Implementation

### trading_bot_smart.py

**Change 1: Update filled amount when fill is detected (~line 3422)**

After `window_state['capture_99c_fill_notified'] = True`, add:

```python
# Update with ACTUAL filled amount (not requested)
if side == 'UP':
    window_state['capture_99c_filled_up'] = filled
else:
    window_state['capture_99c_filled_down'] = filled

# Warn if partial fill
requested = window_state.get('capture_99c_shares', 0)
if abs(filled - requested) > 0.01:
    print(f"[{ts()}] 99c PARTIAL_FILL: Requested {requested}, got {filled:.4f}")
```

**Change 2: Use floor() in execute_hard_stop (~line 1309)**

```python
remaining_shares = math.floor(shares)  # Avoid rounding issues
```

**Change 3: Update version**

```python
BOT_VERSION = {
    "version": "v1.41",
    "codename": "Precise Counter",
    "date": "2026-02-05",
    "changes": "Fix: Track actual filled shares (not requested) to prevent sell failures"
}
```

## Testing

1. Deploy to server
2. Wait for next 99c capture fill
3. Verify log shows actual filled amount
4. If HARD_STOP triggers, verify it uses correct share count

## References

- Bug discovered in window `1770267600` (21:00 PST 2026-02-04)
- Related code: `trading_bot_smart.py:2062-2064`, `trading_bot_smart.py:3397-3426`, `trading_bot_smart.py:1287-1367`
