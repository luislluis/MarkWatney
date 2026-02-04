---
title: Supabase Performance Dashboard
type: feat
date: 2026-02-04
---

# Supabase Performance Dashboard

## Overview

Replace unreliable Google Sheets sync with a Supabase-powered dashboard showing real-time trading performance: per-trade P&L, ROI, and daily summaries.

## Problem Statement

Google Sheets API is returning persistent 503 errors, blocking dashboard sync. The bot already has Supabase integration (`supabase_logger.py`) but it's disabled because `SUPABASE_KEY` is missing from the server.

**Critical Gap Discovered:** The bot logs 99c capture entries (`CAPTURE_99C`) but **never logs the outcome** (WIN/LOSS). Without outcome logging, the dashboard cannot show win rates or realized P&L.

## Proposed Solution

### Phase 1: Fix Supabase Connection (5 min)
1. Add `SUPABASE_KEY` to server `~/.env`
2. Restart bot to enable Supabase logging
3. Verify data flows to Supabase tables

### Phase 2: Add Outcome Logging (30 min)
Add `CAPTURE_99C_WIN` and `CAPTURE_99C_LOSS` event logging at window end when a 99c capture position resolves.

### Phase 3: Create SQL Views (15 min)
Create two views in Supabase dashboard for analytics.

## Technical Approach

### Phase 1: Enable Supabase Connection

**File: Server `~/.env`**
```bash
SUPABASE_KEY=<get from Supabase dashboard>
```

**Verification:**
```bash
tail -50 ~/polybot/bot.log | grep SUPABASE
# Should see: [SUPABASE] Connected!
```

### Phase 2: Add Outcome Logging

**File: `trading_bot_smart.py`**

The bot already tracks 99c capture state in `window_state`:
- `capture_99c_fill_notified` - True when order filled
- `capture_99c_side` - "UP" or "DOWN"
- `capture_99c_shares` - Number of shares
- `capture_99c_price` - Entry price (0.99)

**Add at window end** (after market resolution is known):

```python
# In the window close / resolution handling section
if window_state.get('capture_99c_fill_notified'):
    side = window_state.get('capture_99c_side')
    shares = window_state.get('capture_99c_shares', 0)
    entry_price = window_state.get('capture_99c_price', 0.99)

    # Determine outcome from market resolution
    # If our side won: settlement = $1.00, profit = (1.00 - 0.99) * shares
    # If our side lost: settlement = $0.00, loss = 0.99 * shares

    if our_side_won:
        pnl = (1.00 - entry_price) * shares  # e.g., 0.01 * 6 = $0.06
        event_type = "CAPTURE_99C_WIN"
    else:
        pnl = -entry_price * shares  # e.g., -0.99 * 6 = -$5.94
        event_type = "CAPTURE_99C_LOSS"

    sheets_log_event(event_type, window_id,
                     side=side,
                     shares=shares,
                     price=entry_price,
                     pnl=pnl,
                     details=json.dumps({
                         "outcome": "WIN" if our_side_won else "LOSS",
                         "settlement_price": 1.00 if our_side_won else 0.00
                     }))
```

### Phase 3: SQL Views in Supabase

**View 1: `trade_details`**
```sql
CREATE VIEW trade_details AS
SELECT
  "Timestamp" as timestamp,
  "Window ID" as window_id,
  "Side" as side,
  CAST("Shares" AS NUMERIC) as shares,
  CAST("Price" AS NUMERIC) as entry_price,
  CAST("PnL" AS NUMERIC) as profit_loss,
  CASE
    WHEN CAST("Shares" AS NUMERIC) > 0 AND CAST("Price" AS NUMERIC) > 0
    THEN ROUND(CAST("PnL" AS NUMERIC) / (CAST("Shares" AS NUMERIC) * CAST("Price" AS NUMERIC)) * 100, 2)
    ELSE 0
  END as roi_percent,
  "Event" as event_type,
  "Details"::json->>'outcome' as resolution
FROM "Polymarket Bot Log - Events"
WHERE "Event" IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS')
ORDER BY "Timestamp" DESC;
```

**View 2: `daily_summary`**
```sql
CREATE VIEW daily_summary AS
SELECT
  DATE("Timestamp") as trade_date,
  COUNT(*) as total_trades,
  SUM(CASE WHEN CAST("PnL" AS NUMERIC) > 0 THEN 1 ELSE 0 END) as wins,
  SUM(CASE WHEN CAST("PnL" AS NUMERIC) <= 0 THEN 1 ELSE 0 END) as losses,
  ROUND(
    SUM(CASE WHEN CAST("PnL" AS NUMERIC) > 0 THEN 1 ELSE 0 END)::numeric /
    NULLIF(COUNT(*), 0) * 100, 1
  ) as win_rate_pct,
  SUM(CAST("PnL" AS NUMERIC)) as total_pnl,
  SUM(CAST("Shares" AS NUMERIC) * CAST("Price" AS NUMERIC)) as total_invested,
  ROUND(
    SUM(CAST("PnL" AS NUMERIC)) /
    NULLIF(SUM(CAST("Shares" AS NUMERIC) * CAST("Price" AS NUMERIC)), 0) * 100, 2
  ) as daily_roi_pct
FROM "Polymarket Bot Log - Events"
WHERE "Event" IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS')
GROUP BY DATE("Timestamp")
ORDER BY trade_date DESC;
```

## Acceptance Criteria

### Phase 1: Connection
- [ ] `SUPABASE_KEY` added to server `~/.env`
- [ ] Bot startup shows `[SUPABASE] Connected!`
- [ ] Ticks appear in `Polymarket Bot Log - Ticks` table

### Phase 2: Outcome Logging
- [x] `CAPTURE_99C_WIN` events logged when 99c capture wins
- [x] `CAPTURE_99C_LOSS` events logged when 99c capture loses
- [x] Events include: side, shares, price, pnl, outcome in details

### Phase 3: Dashboard Views
- [ ] `trade_details` view created and queryable
- [ ] `daily_summary` view created and queryable
- [ ] Views accessible in Supabase Table Editor

## Dependencies & Prerequisites

1. **SUPABASE_KEY** - Need access to Supabase project to get API key
   - URL: `https://qszosdrmnoglrkttdevz.supabase.co`
   - Go to: Project Settings > API > `anon` or `service_role` key

2. **Market Resolution Logic** - Need to identify where in `trading_bot_smart.py` the market outcome is determined
   - Look for: CLOB API resolution check, or price-based winner determination

## Risk Analysis

| Risk | Mitigation |
|------|------------|
| Missing SUPABASE_KEY | User provides from Supabase dashboard |
| Outcome logic unclear | Research existing resolution code in bot |
| Table name has spaces | Use quoted identifiers in SQL |
| Data type mismatch | Cast strings to numeric in views |

## Open Questions

1. **Where does the bot determine market winner?** Need to find the resolution logic to know when to log WIN/LOSS.

2. **Historical data?** Start fresh from today, or backfill past trades?

3. **Retry logic?** Should we add exponential backoff to `supabase_logger.py` like `sheets_logger.py` has?

## Success Metrics

- Dashboard shows today's trades within 5 minutes of implementation
- Win/loss rate visible and accurate
- Daily P&L totals match manual calculations

## References

- **Brainstorm:** `docs/brainstorms/2026-02-04-supabase-dashboard-brainstorm.md`
- **Supabase Logger:** `supabase_logger.py`
- **Trading Bot:** `trading_bot_smart.py`
- **Supabase URL:** `https://qszosdrmnoglrkttdevz.supabase.co`
