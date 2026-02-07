# Brainstorm: Guaranteed Fill Share Count

**Date:** 2026-02-07
**Status:** Ready for implementation

## What We're Building

Fix the share count problem **at the source**: after fill detection, sleep 2-3 seconds to let the Position API catch up, then query it for the exact on-chain balance. This replaces the current approach (query Position API immediately, fall back to unreliable order API when it returns 0).

## Why This Matters

**Incident (2026-02-06, window 1770492600):**
1. 99c capture fills for DOWN 6 shares
2. Position API queried immediately → returns 0 (not yet propagated)
3. Falls back to order API `size_matched` = 6.0000 (unreliable)
4. OB exit triggers in the same second (ob_negative_ticks 0→3 instantly)
5. FOK tries to sell 6.0 shares → "not enough balance" (actual was ~5.99)
6. Bot treats "not enough balance" as "already sold" → marks EXITED
7. Bot still holds ~5.99 DOWN shares → DOWN loses → **~$5.94 loss**

The Position API returned 0 because it was queried <1 second after the CLOB fill. The on-chain state hadn't propagated yet.

## Key Decisions

### 1. Sleep 2-3 seconds after fill, then retry Position API

After `get_order_status()` detects filled > 0:
1. `time.sleep(2)` — let Position API catch up
2. Query Position API
3. If still 0, `time.sleep(1)` and retry (up to 3 total attempts)
4. If all retries return 0, fall back to order API value (with floor)

### 2. Sleep naturally blocks OB exit

During the 2-3 second sleep, the main loop is blocked — OB exit check can't run. This prevents the instant 0→3 ob_negative_ticks burst that caused the false exit.

No separate OB cooldown flag needed — the sleep IS the cooldown.

### 3. Floor still applies as last resort

If Position API never returns (all retries exhausted), the `math.floor()` in `place_fok_market_sell()` (v1.57) still protects against the 6.00 → 5.99 precision issue. It's the safety net behind the safety net.

## What Stays Unchanged

- `get_order_status()` — still used for fill DETECTION (filled > 0 check)
- `place_fok_market_sell()` floor — keeps the 2dp floor as backup
- OB exit logic — no changes, sleep timing is sufficient cooldown
- `ob_negative_ticks` — not reset, just naturally can't increment during sleep

## Open Questions

None — ready for implementation.
