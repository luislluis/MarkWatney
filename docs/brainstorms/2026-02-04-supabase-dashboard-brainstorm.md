# Supabase Performance Dashboard Brainstorm

**Date:** 2026-02-04
**Status:** Ready for implementation

## What We're Building

A real-time trading performance dashboard powered by Supabase, replacing the unreliable Google Sheets sync. The dashboard will show:

1. **Per-trade breakdown**: Each 99c capture trade with $ P&L, % ROI, and resolution status
2. **Daily summaries**: Total P&L, trade count, win rate, ROI for each day
3. **Verification**: Trade outcomes verified via CLOB API resolution data

## Why This Approach

**Supabase Dashboard + SQL Views** chosen because:
- Zero frontend code required
- Instant setup (Supabase already integrated)
- Real-time data (bot already has Supabase logger)
- SQL views provide flexible aggregations
- Built-in charts and table views

**What's already working:**
- `supabase_logger.py` - complete with tick, event, and activity logging
- Dual-logging pattern in bot (Sheets + Supabase)
- Background thread pattern for non-blocking uploads
- Three tables defined: Ticks, Events, Activity

**What's broken:**
- `SUPABASE_KEY` missing from server `~/.env`
- Without the key, all Supabase logging silently skips

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Dashboard tool | Supabase built-in | Zero code, instant |
| Data source | Existing Supabase tables | Already integrated |
| Fix required | Add SUPABASE_KEY to server | Single env var fix |
| Views needed | 2 SQL views | daily_summary, trade_details |

## Implementation Steps

### Step 1: Fix Supabase Connection
1. Get SUPABASE_KEY from Supabase dashboard (Project Settings > API)
2. Add to server `~/.env`: `SUPABASE_KEY=<key>`
3. Restart bot - will auto-connect

### Step 2: Create SQL Views in Supabase

**View 1: `trade_details`**
```sql
-- Per-trade breakdown with P&L and ROI
SELECT
  timestamp,
  window_id,
  side,
  shares,
  price as entry_price,
  pnl as profit_loss,
  ROUND((pnl / (shares * price)) * 100, 2) as roi_percent,
  details->>'outcome' as resolution,
  details->>'outcome_price' as settlement_price
FROM "Polymarket Bot Log - Events"
WHERE event_type IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS', 'CAPTURE_99C_FILL')
ORDER BY timestamp DESC;
```

**View 2: `daily_summary`**
```sql
-- Daily P&L rollup
SELECT
  DATE(timestamp) as trade_date,
  COUNT(*) as total_trades,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
  SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
  ROUND(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1) as win_rate,
  SUM(pnl) as total_pnl,
  SUM(shares * price) as total_invested,
  ROUND(SUM(pnl) / SUM(shares * price) * 100, 2) as daily_roi
FROM "Polymarket Bot Log - Events"
WHERE event_type IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS')
GROUP BY DATE(timestamp)
ORDER BY trade_date DESC;
```

### Step 3: Create Dashboard in Supabase
1. Go to Supabase Dashboard > SQL Editor
2. Create the views above
3. Go to Table Editor > Select view > Enable "Realtime"
4. Use built-in table view with filters, or create custom dashboard

## Open Questions

1. **SUPABASE_KEY location** - Do you have access to the Supabase project to get the key?
2. **Historical data** - Start fresh from today, or backfill from Google Sheets?
3. **Resolution logging** - Bot logs outcomes, but need to verify the event types match

## Supabase Project Details

- **URL**: `https://qszosdrmnoglrkttdevz.supabase.co`
- **Tables**:
  - `Polymarket Bot Log - Ticks` (per-second data)
  - `Polymarket Bot Log - Events` (trades, fills, outcomes)
  - `Polymarket Bot Log - Activity` (all bot actions)

## Next Steps

1. Get SUPABASE_KEY and add to server
2. Restart bot to enable Supabase logging
3. Create SQL views in Supabase dashboard
4. Verify data is flowing
5. Customize dashboard view as needed
