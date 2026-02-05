-- Supabase SQL Views for Dashboard (EST Timezone)
-- Run these in Supabase SQL Editor: https://supabase.com/dashboard/project/qszosdrmnoglrkttdevz/sql
--
-- FIX (2026-02-05): Changed from CAPTURE_99C (order placed) to CAPTURE_FILL (order filled).
-- CAPTURE_99C fires at order placement, so unfilled orders showed as "pending" forever
-- and logged the ask price (up to 100c) instead of the actual fill price (99c).
-- CAPTURE_FILL only fires when the order actually fills, with the real fill price.

-- Drop existing views first
DROP VIEW IF EXISTS trade_details;
DROP VIEW IF EXISTS daily_summary;

-- View 1: trade_details - Shows all filled trades with status (PENDING/WIN/LOSS)
-- Uses CAPTURE_FILL event (actual fills only), joins with WIN/LOSS for outcomes
CREATE VIEW trade_details AS
WITH orders AS (
  SELECT
    ("Timestamp"::timestamptz AT TIME ZONE 'America/New_York') as timestamp,
    "Window ID" as window_id,
    "Side" as side,
    CAST("Shares" AS NUMERIC) as shares,
    CAST("Price" AS NUMERIC) as entry_price
  FROM "Polymarket Bot Log - Events"
  WHERE "Event" = 'CAPTURE_FILL'
),
outcomes AS (
  SELECT
    "Window ID" as window_id,
    CAST("PnL" AS NUMERIC) as profit_loss,
    CASE
      WHEN "Event" = 'CAPTURE_99C_WIN' THEN 'WIN'
      WHEN "Event" = 'CAPTURE_99C_LOSS' THEN 'LOSS'
    END as status
  FROM "Polymarket Bot Log - Events"
  WHERE "Event" IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS')
)
SELECT
  o.timestamp,
  o.window_id,
  o.side,
  o.shares,
  o.entry_price,
  COALESCE(out.status, 'PENDING') as status,
  COALESCE(out.profit_loss, 0) as profit_loss
FROM orders o
LEFT JOIN outcomes out ON o.window_id = out.window_id
ORDER BY o.timestamp DESC;

-- View 2: daily_summary - Aggregates by day (EST) with pending count and ROI
-- Uses CAPTURE_FILL to only count filled trades
-- Joins outcomes by window_id to orders first, then aggregates by order date
-- This ensures wins/losses are counted on the day the trade was PLACED, not resolved
CREATE VIEW daily_summary AS
WITH orders AS (
  SELECT
    DATE(("Timestamp"::timestamptz AT TIME ZONE 'America/New_York')) as trade_date,
    "Window ID" as window_id,
    CAST("Shares" AS NUMERIC) as shares,
    CAST("Price" AS NUMERIC) as price
  FROM "Polymarket Bot Log - Events"
  WHERE "Event" = 'CAPTURE_FILL'
),
outcomes AS (
  SELECT
    "Window ID" as window_id,
    "Event" as event,
    CAST("PnL" AS NUMERIC) as pnl
  FROM "Polymarket Bot Log - Events"
  WHERE "Event" IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS')
),
-- Join orders with outcomes by window_id, keeping the ORDER's trade_date
order_outcomes AS (
  SELECT
    o.trade_date,
    o.window_id,
    o.shares,
    o.price,
    out.event,
    out.pnl
  FROM orders o
  LEFT JOIN outcomes out ON o.window_id = out.window_id
)
SELECT
  trade_date,
  COUNT(DISTINCT window_id) as total_trades,
  COUNT(DISTINCT CASE WHEN event = 'CAPTURE_99C_WIN' THEN window_id END) as wins,
  COUNT(DISTINCT CASE WHEN event = 'CAPTURE_99C_LOSS' THEN window_id END) as losses,
  COUNT(DISTINCT window_id) - COUNT(DISTINCT CASE WHEN event IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS') THEN window_id END) as pending,
  ROUND(
    COUNT(DISTINCT CASE WHEN event = 'CAPTURE_99C_WIN' THEN window_id END)::numeric /
    NULLIF(COUNT(DISTINCT CASE WHEN event IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS') THEN window_id END), 0) * 100, 1
  ) as win_rate_pct,
  COALESCE(SUM(pnl), 0) as total_pnl,
  ROUND(
    COALESCE(SUM(pnl), 0) / NULLIF(SUM(shares * price) / COUNT(DISTINCT window_id), 0) * 100, 1
  ) as roi_pct
FROM order_outcomes
GROUP BY trade_date
ORDER BY trade_date DESC;
