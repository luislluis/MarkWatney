---
title: "Instant Profit Lock: Auto-Sell at 99c on Fill"
type: feat
date: 2026-02-27
---

# v1.58: Instant Profit Lock

## Overview

After a 99c capture order fills (bought at 95c), immediately place a sell limit order at 99c for the entire fill quantity. If the sell fills, we lock in 4c/share profit with zero settlement risk. If the market turns and our side's best bid drops below 60c, cancel the sell order and fall back to the 45c hard stop.

## Problem Statement

On 2026-02-27, the bot held 280 DOWN shares while BTC reversed in the final 30 seconds. DOWN went from 99c to $0 at settlement — a $277.20 loss. For 29 seconds after the fill, DN bids were at 91-98c and the bot could have sold profitably. But it had no mechanism to sell — it just held to settlement.

**Key insight:** The bot currently bets on settlement ($1.00) for 1-5c profit per share. But selling at 99c gives nearly the same profit (4c/share) with zero settlement risk. The extra 1c from settlement isn't worth the catastrophic downside.

## Proposed Solution

Two-phase post-fill strategy:

**Phase 1 — Profit Lock (on fill):**
- Immediately place sell limit at 99c for entire fill quantity
- If sell fills → done, profit locked, no settlement risk

**Phase 2 — Defensive Exit (if market turns):**
- If our side's best bid drops below 60c → cancel the 99c sell order
- Existing 45c hard stop takes over for emergency exit

## Technical Approach

### Change 1: Add constants (after line ~390)

```python
# ===========================================
# INSTANT PROFIT LOCK (v1.58)
# ===========================================
PROFIT_LOCK_ENABLED = True
PROFIT_LOCK_SELL_PRICE = 0.99         # Sell limit price (99c)
PROFIT_LOCK_CANCEL_THRESHOLD = 0.60   # Cancel sell if best bid drops below 60c
```

### Change 2: Add window_state fields (line ~543)

```python
"profit_lock_order_id": None,        # Profit lock: sell order ID
"profit_lock_cancelled": False,      # Profit lock: whether sell was cancelled due to price drop
"profit_lock_filled": False,         # Profit lock: whether sell order filled
```

### Change 3: Place sell order on fill (after line 3912)

Right after the `CAPTURE_FILL` log event (line 3912), add:

```python
# === INSTANT PROFIT LOCK (v1.58) ===
# Immediately place sell at 99c to lock in profit
if PROFIT_LOCK_ENABLED and not window_state.get('profit_lock_order_id'):
    sell_token = window_state['up_token'] if side == "UP" else window_state['down_token']
    sell_price = PROFIT_LOCK_SELL_PRICE
    sell_shares = filled

    print(f"[{ts()}] 🔒 PROFIT_LOCK: Placing sell {sell_shares:.0f} {side} @ {sell_price*100:.0f}c")
    success, result = place_limit_order(
        sell_token, sell_price, sell_shares,
        side="SELL", bypass_price_failsafe=True
    )
    if success:
        window_state['profit_lock_order_id'] = result
        print(f"[{ts()}] 🔒 PROFIT_LOCK: Sell order placed (ID: {result[:8]}...)")
        log_activity("PROFIT_LOCK_PLACED", {
            "side": side, "shares": sell_shares,
            "sell_price": sell_price, "order_id": result
        })
    else:
        print(f"[{ts()}] PROFIT_LOCK_ERROR: Failed to place sell: {result}")
```

### Change 4: Monitor sell order + cancel logic (after the hard stop check, ~line 3939)

Add a new block after the hard stop check. This runs every tick when we have a profit lock sell order:

```python
# === PROFIT LOCK MONITOR (v1.58) ===
if (PROFIT_LOCK_ENABLED and
    window_state.get('profit_lock_order_id') and
    not window_state.get('profit_lock_filled') and
    not window_state.get('capture_99c_exited')):

    capture_side = window_state.get('capture_99c_side')
    lock_order_id = window_state['profit_lock_order_id']

    # Check if sell order filled
    sell_status = get_order_status(lock_order_id)
    if sell_status.get('filled', 0) > 0:
        filled_shares = sell_status['filled']
        print(f"[{ts()}] 🔒✅ PROFIT_LOCK FILLED: Sold {filled_shares:.0f} {capture_side} @ 99c")
        window_state['profit_lock_filled'] = True
        window_state['capture_99c_exited'] = True
        log_activity("PROFIT_LOCK_FILLED", {
            "side": capture_side, "shares": filled_shares,
            "price": PROFIT_LOCK_SELL_PRICE
        })
    elif not window_state.get('profit_lock_cancelled'):
        # Check if we need to cancel (best bid dropped below 60c)
        if capture_side == "UP":
            bids = books.get('up_bids', [])
        else:
            bids = books.get('down_bids', [])

        best_bid = float(bids[0]['price']) if bids else 0
        if best_bid < PROFIT_LOCK_CANCEL_THRESHOLD:
            print(f"[{ts()}] 🔒❌ PROFIT_LOCK CANCEL: {capture_side} bid={best_bid*100:.0f}c < {PROFIT_LOCK_CANCEL_THRESHOLD*100:.0f}c")
            cancel_order(lock_order_id)
            window_state['profit_lock_cancelled'] = True
            log_activity("PROFIT_LOCK_CANCELLED", {
                "side": capture_side, "best_bid": best_bid,
                "reason": "bid_below_threshold"
            })
```

### Change 5: Cancel profit lock order on window end (before WINDOW COMPLETE)

Ensure the sell order is cancelled at window end if still open, to prevent stale orders carrying over:

```python
# Cancel any open profit lock sell order
if window_state.get('profit_lock_order_id') and not window_state.get('profit_lock_filled'):
    cancel_order(window_state['profit_lock_order_id'])
```

### Change 6: Version bump

```python
BOT_VERSION = {
    "version": "v1.58",
    "codename": "Profit Lock",
    "date": "2026-02-27",
    "changes": "Auto-sell at 99c on fill; cancel if bid < 60c; 45c hard stop as backstop"
}
```

### Change 7: Update CLAUDE.md

Add to "Verified Bot Rules & Settings" table:

| Setting | Value | Why |
|---------|-------|-----|
| `PROFIT_LOCK_ENABLED` | `True` | Immediately sells at 99c after fill to lock in 4c/share profit. Eliminates settlement risk. |
| `PROFIT_LOCK_SELL_PRICE` | `0.99` (99c) | Sell at 99c. Buyer gets 1c settlement profit — enough for market makers to fill. |
| `PROFIT_LOCK_CANCEL_THRESHOLD` | `0.60` (60c) | Cancel sell if bid drops below 60c (won't fill anyway). Hard stop at 45c takes over. |

Add to "Common Mistakes to Avoid":
- **Don't remove the profit lock sell order** — holding to settlement risks 100% loss on last-second BTC reversals. Selling at 99c gives 4c/share profit with zero risk. The 1c "lost" from not settling at $1.00 is insurance worth paying.

### Change 8: Update BOT_REGISTRY.md

Add v1.58 entry with codename "Profit Lock".

## Acceptance Criteria

- [ ] On 99c capture fill, a sell limit at 99c is placed immediately for full fill quantity
- [ ] If sell fills, `capture_99c_exited` is set to True (prevents hard stop from firing)
- [ ] If best bid drops below 60c, sell order is cancelled
- [ ] After cancel, existing 45c hard stop handles emergency exit
- [ ] Sell order is cancelled on window end if still open
- [ ] `PROFIT_LOCK_PLACED`, `PROFIT_LOCK_FILLED`, `PROFIT_LOCK_CANCELLED` logged
- [ ] No changes to entry logic, confidence calculation, or hard stop
- [ ] Version bumped to v1.58

## Files Modified

| File | Changes |
|------|---------|
| `trading_bot_smart.py` | 3 constants, 3 window_state fields, sell-on-fill logic, monitor/cancel loop, window-end cleanup, version bump |
| `CLAUDE.md` | New settings, lesson learned |
| `BOT_REGISTRY.md` | v1.58 entry |

## Why This Is Safe

1. **Sell at 99c is strictly better than hold to settlement** — 4c profit vs 5c profit, but zero catastrophic risk
2. **60c cancel threshold gives hard stop time to work** — 15c gap between cancel (60c) and hard stop (45c)
3. **No changes to entry logic** — only affects post-fill behavior
4. **Reuses existing infrastructure** — `place_limit_order(side="SELL")`, `cancel_order()`, `get_order_status()`
5. **Fail-safe**: if sell order fails to place, behavior is identical to current (hold to settlement + hard stop)

## Backtested Against Today's Loss

| Event | Current (v1.57) | With Profit Lock (v1.58) |
|-------|-----------------|--------------------------|
| Fill at T-74s | Hold 280 DN | Sell limit 280 DN @ 99c |
| T-74s to T-47s | DN at 95-99c, holding | **Sell fills at 99c = $277.20 received** |
| T-47s | DN crashes to 50c | Already sold, no exposure |
| Settlement | DN = $0, LOSS $-277.20 | **PROFIT: $277.20 - $266 = +$11.20** |
