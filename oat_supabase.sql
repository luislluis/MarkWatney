-- Supabase table definitions for Strategy Extractor Bot dashboard
-- Run these in Supabase SQL Editor to set up tables + RLS policies

-- Observations: one row per 15-min window
CREATE TABLE IF NOT EXISTS oat_observations (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    window_start BIGINT NOT NULL,
    target_traded BOOLEAN DEFAULT FALSE,
    target_sides TEXT,
    target_total_buys INTEGER DEFAULT 0,
    target_total_sells INTEGER DEFAULT 0,
    target_up_shares REAL DEFAULT 0,
    target_down_shares REAL DEFAULT 0,
    target_up_avg_price REAL DEFAULT 0,
    target_down_avg_price REAL DEFAULT 0,
    target_up_total_usdc REAL DEFAULT 0,
    target_down_total_usdc REAL DEFAULT 0,
    target_first_buy_offset_secs INTEGER,
    target_first_buy_side TEXT,
    target_leg_gap_secs INTEGER,
    target_combined_cost REAL,
    target_maker_count INTEGER DEFAULT 0,
    target_taker_count INTEGER DEFAULT 0,
    up_ask_at_entry REAL,
    down_ask_at_entry REAL,
    ob_imbalance_at_entry REAL,
    time_remaining_at_entry INTEGER,
    outcome TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oat_obs_window ON oat_observations(window_start DESC);

-- Analysis results: one row per analyzer run
CREATE TABLE IF NOT EXISTS oat_analysis (
    id BIGSERIAL PRIMARY KEY,
    run_timestamp BIGINT NOT NULL,
    sample_start TEXT,
    sample_end TEXT,
    sample_size INTEGER,
    entry_timing_confidence REAL DEFAULT 0,
    side_selection_confidence REAL DEFAULT 0,
    pricing_confidence REAL DEFAULT 0,
    sizing_confidence REAL DEFAULT 0,
    arb_structure_confidence REAL DEFAULT 0,
    exit_behavior_confidence REAL DEFAULT 0,
    overall_readiness REAL DEFAULT 0,
    entry_timing_data JSONB,
    side_selection_data JSONB,
    pricing_data JSONB,
    sizing_data JSONB,
    arb_structure_data JSONB,
    exit_behavior_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oat_analysis_ts ON oat_analysis(run_timestamp DESC);

-- Fills: individual trades by target
CREATE TABLE IF NOT EXISTS oat_fills (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL,
    tx_hash TEXT UNIQUE NOT NULL,
    timestamp BIGINT NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    usdc_size REAL NOT NULL,
    fill_type TEXT,
    sequence_in_window INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oat_fills_slug ON oat_fills(slug, timestamp);
CREATE INDEX IF NOT EXISTS idx_oat_fills_ts ON oat_fills(timestamp DESC);

-- RLS: enable read-only access with anon key
ALTER TABLE oat_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE oat_analysis ENABLE ROW LEVEL SECURITY;
ALTER TABLE oat_fills ENABLE ROW LEVEL SECURITY;

-- Allow anon read
CREATE POLICY "anon_read_observations" ON oat_observations FOR SELECT USING (true);
CREATE POLICY "anon_read_analysis" ON oat_analysis FOR SELECT USING (true);
CREATE POLICY "anon_read_fills" ON oat_fills FOR SELECT USING (true);

-- Allow service role to insert/update (the observer uses service key)
CREATE POLICY "service_write_observations" ON oat_observations FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_write_analysis" ON oat_analysis FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_write_fills" ON oat_fills FOR ALL USING (true) WITH CHECK (true);
