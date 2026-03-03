-- Supervisor Bot — Supabase Tables
-- Run in: https://supabase.com/dashboard/project/qszosdrmnoglrkttdevz/sql

-- ===========================================
-- Window Audits table
-- ===========================================
CREATE TABLE IF NOT EXISTS "Supervisor - Window Audits" (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  window_id text UNIQUE NOT NULL,
  audit_timestamp timestamptz NOT NULL DEFAULT now(),
  window_start timestamptz NOT NULL,
  classification text NOT NULL,
  severity text NOT NULL DEFAULT 'ok',
  strategy_mode text,
  bot_status text,
  bot_up_shares numeric DEFAULT 0,
  bot_down_shares numeric DEFAULT 0,
  api_up_shares numeric DEFAULT 0,
  api_down_shares numeric DEFAULT 0,
  bot_fill_price numeric,
  api_fill_price numeric,
  pnl_bot numeric DEFAULT 0,
  pnl_verified numeric DEFAULT 0,
  entry_confidence numeric,
  entry_ttl integer,
  exit_type text,
  exit_price numeric,
  market_outcome text,
  ob_depth_at_entry numeric,
  diagnosis text,
  recommendation text,
  observation_complete boolean DEFAULT true,
  api_verified boolean DEFAULT true,
  details jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);

-- Enable RLS but allow anon reads (dashboard) and service writes
ALTER TABLE "Supervisor - Window Audits" ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow anon read" ON "Supervisor - Window Audits"
  FOR SELECT USING (true);

CREATE POLICY "Allow service write" ON "Supervisor - Window Audits"
  FOR ALL USING (true);

-- Enable Realtime for live dashboard updates
ALTER publication supabase_realtime ADD TABLE "Supervisor - Window Audits";


-- ===========================================
-- Daily Summary table
-- ===========================================
CREATE TABLE IF NOT EXISTS "Supervisor - Daily Summary" (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  date date UNIQUE NOT NULL,
  total_windows integer DEFAULT 0,
  idle_windows integer DEFAULT 0,
  traded_windows integer DEFAULT 0,
  clean_wins integer DEFAULT 0,
  unpaired integer DEFAULT 0,
  bails integer DEFAULT 0,
  hard_stops integer DEFAULT 0,
  danger_exits integer DEFAULT 0,
  profit_locks integer DEFAULT 0,
  pnl_verified numeric DEFAULT 0,
  pnl_bot_reported numeric DEFAULT 0,
  pair_rate numeric DEFAULT 0,
  win_rate numeric DEFAULT 0,
  pattern_analysis text,
  recommendations text,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE "Supervisor - Daily Summary" ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow anon read" ON "Supervisor - Daily Summary"
  FOR SELECT USING (true);

CREATE POLICY "Allow service write" ON "Supervisor - Daily Summary"
  FOR ALL USING (true);

ALTER publication supabase_realtime ADD TABLE "Supervisor - Daily Summary";
