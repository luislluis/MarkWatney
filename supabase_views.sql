-- Supabase SQL Views for Pending Trades Display (EST Timezone)
-- Run these in Supabase SQL Editor: https://supabase.com/dashboard/project/qszosdrmnoglrkttdevz/sql

-- Drop existing views first
DROP VIEW IF EXISTS trade_details;
DROP VIEW IF EXISTS daily_summary;

-- View 1: trade_details - Shows all trades with status (PENDING/WIN/LOSS)
-- Uses CAPTURE_99C event for order placement, joins with WIN/LOSS for outcomes
CREATE VIEW trade_details AS
WITH orders AS (
  SELECT
    ("Timestamp"::timestamptz AT TIME ZONE 'America/New_York') as timestamp,
    "Window ID" as window_id,
    "Side" as side,
    CAST("Shares" AS NUMERIC) as shares,
    CAST("Price" AS NUMERIC) as entry_price
  FROM "Polymarket Bot Log - Events"
  WHERE "Event" = 'CAPTURE_99C'
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
CREATE VIEW daily_summary AS
WITH orders AS (
  SELECT
    DATE(("Timestamp"::timestamptz AT TIME ZONE 'America/New_York')) as trade_date,
    "Window ID" as window_id,
    CAST("Shares" AS NUMERIC) as shares,
    CAST("Price" AS NUMERIC) as price
  FROM "Polymarket Bot Log - Events"
  WHERE "Event" = 'CAPTURE_99C'
),
outcomes AS (
  SELECT
    DATE(("Timestamp"::timestamptz AT TIME ZONE 'America/New_York')) as trade_date,
    "Window ID" as window_id,
    "Event" as event,
    CAST("PnL" AS NUMERIC) as pnl
  FROM "Polymarket Bot Log - Events"
  WHERE "Event" IN ('CAPTURE_99C_WIN', 'CAPTURE_99C_LOSS')
),
daily_orders AS (
  SELECT
    trade_date,
    COUNT(DISTINCT window_id) as total_trades,
    SUM(shares * price) as total_invested
  FROM orders
  GROUP BY trade_date
),
daily_outcomes AS (
  SELECT
    trade_date,
    COUNT(DISTINCT CASE WHEN event = 'CAPTURE_99C_WIN' THEN window_id END) as wins,
    COUNT(DISTINCT CASE WHEN event = 'CAPTURE_99C_LOSS' THEN window_id END) as losses,
    SUM(pnl) as total_pnl
  FROM outcomes
  GROUP BY trade_date
)
SELECT
  o.trade_date,
  o.total_trades,
  COALESCE(out.wins, 0) as wins,
  COALESCE(out.losses, 0) as losses,
  o.total_trades - COALESCE(out.wins, 0) - COALESCE(out.losses, 0) as pending,
  ROUND(
    COALESCE(out.wins, 0)::numeric /
    NULLIF(COALESCE(out.wins, 0) + COALESCE(out.losses, 0), 0) * 100, 1
  ) as win_rate_pct,
  COALESCE(out.total_pnl, 0) as total_pnl,
  ROUND(
    COALESCE(out.total_pnl, 0) / NULLIF(o.total_invested / o.total_trades, 0) * 100, 1
  ) as roi_pct
FROM daily_orders o
LEFT JOIN daily_outcomes out ON o.trade_date = out.trade_date
ORDER BY o.trade_date DESC;
