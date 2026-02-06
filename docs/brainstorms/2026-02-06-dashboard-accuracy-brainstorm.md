# Dashboard Data Accuracy - Brainstorm

**Date:** 2026-02-06
**Status:** Active

## What's Wrong

The dashboard shows inaccurate P&L for today. Ground truth from the Polymarket Activity API shows **-$2.04** (56W/3L), but the dashboard misses 2 of the 3 losses, likely showing a small positive P&L instead.

## Root Cause

The "assume WIN for old pending trades" fallback (added to handle markets removed from CLOB API) incorrectly marks **real losses** as wins when:
1. Bot fails to log CAPTURE_99C_LOSS event to Supabase (crash/restart during window)
2. CLOB API returns 404 (market expired)
3. Trade is >2 hours old → forced to WIN

Today's specific cases:
- Window `1770361200`: DOWN@98c, market resolved UP = **-$5.88 loss, shown as +$0.12 win**
- Window `1770372000`: DOWN@98c, market resolved UP = **-$5.88 loss, shown as +$0.12 win**

## Proposed Fix

Replace the "assume WIN" fallback with **Activity API redemption check**:
- `GET /activity?user={wallet}&limit=500` returns TRADE + REDEEM + MAKER_REBATE events
- If a trade's slug has a matching REDEEM event → WIN
- If no REDEEM and market is old → LOSS (not redeemed = lost)
- This is reliable because redemptions are on-chain facts, not bot logging

## Additional Bugs Found

1. **windowStatus priority**: WIN can't override PENDING in multi-fill windows
2. **Supabase PnL ignored in primary path** (recalculated from fills — actually more accurate)
3. **500-record limit**: silent data loss as volume grows

## Key Decisions

- Use Activity API `/activity` as the authoritative source for outcome resolution
- Remove the "assume WIN" fallback entirely
- Fix windowStatus priority to allow WIN to override PENDING
