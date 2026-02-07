# Brainstorm: Exact Fill Amount Tracking

**Date:** 2026-02-06
**Status:** Ready for planning

## What We're Building

Fix the bot to track the **exact** number of shares after a 99c capture fill, and use that exact number when selling. Currently `size_matched` from the order API is unreliable — it may report 6.00 when actual balance is 5.99. This causes "not enough balance" errors during exits, which the bot misinterprets as "shares already sold," leaving shares stuck.

## Why This Matters

**The bug chain:**
1. Bot places 99c buy for 6 shares
2. py-clob-client's `round_down(floor)` on makerAmount causes ~0.01-0.02 fewer shares to be purchased
3. `size_matched` from order API may still report 6.00 (known bug: [Issue #245](https://github.com/Polymarket/py-clob-client/issues/245))
4. Bot stores 6.0 in `capture_99c_filled_up/down`
5. Exit tries to sell 6.0 shares → "not enough balance"
6. Hard stop treats "not enough balance" as success (shares already sold) → stops retrying
7. Bot thinks it exited but STILL HOLDS 5.99 shares

**Financial impact:** Documented $5.94 loss on window 1770267600 from this exact issue.

## Root Cause (from py-clob-client source)

```python
# In py-clob-client helpers.py:
def round_down(x: float, sig_digits: int) -> float:
    return floor(x * (10**sig_digits)) / (10**sig_digits)
```

When `6.0 * 0.99 = 5.94` USDC, floating point + floor can produce `5,939,999` raw units instead of `5,940,000`, resulting in ~5.99 shares instead of 6.00.

**CLOB precision constraints:**
- Share size: 2 decimal places (6.00, 5.99, etc.)
- Price: 2 decimal places
- Amount (USDC): 4 decimal places
- On-chain tokens: 6 decimal places

## Key Decisions

### 1. Use Position API as ground truth (not order API)

The Position API (`data-api.polymarket.com/positions`) returns the **actual on-chain balance**. The order API's `size_matched` is unreliable per Issue #245.

The bot already has `verify_position_from_api()` at line 1154 that does this.

### 2. Query position after fill detection, store exact amount

After `get_order_status()` detects `filled > 0`, immediately query position API. Use the position API's value as the authoritative share count.

### 3. Floor the sell amount for safety

When selling, use `math.floor(shares * 100) / 100` to round down to 2 decimal places (matching CLOB's size precision). This ensures we never try to sell more than we have.

Example: Position API says 5.9934 shares → floor to 5.99 → sell 5.99

### 4. Log discrepancies between order API and position API

When `size_matched` differs from actual position, log a warning:
```
FILL_PRECISION: order says 6.00, position API says 5.99 (delta=0.01)
```

### 5. Fix affects both fill detection AND exit paths

- **Fill detection** (line 1970): Store position API value
- **execute_99c_early_exit** (line 1279): Reads `capture_99c_filled_*` — automatically fixed
- **execute_hard_stop** (line 992): Reads `capture_99c_filled_*` — automatically fixed
- **place_fok_market_sell** (line 896): Add floor to shares parameter

## What Stays Unchanged

- `get_order_status()` — still used for fill DETECTION (filled > 0)
- `verify_position_from_api()` — already exists, just needs to be called at the right time
- Exit function logic — they read from `capture_99c_filled_*` which will now have the correct value

## Approach: Two Touch Points

**Touch 1: Fill detection (main loop ~line 1970)**
After detecting fill via order status, query position API, store exact balance.

**Touch 2: FOK sell (place_fok_market_sell ~line 896)**
Floor the shares parameter to 2 decimal places before creating the market order.

This is the minimal change — fix the source of truth (fill tracking) and add safety at the consumption point (sell amount).

## Open Questions

None — ready for `/workflows:plan`.
