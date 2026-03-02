---
title: "feat: Strategy Extractor Bot — Reverse-Engineer Oat's Trading Rules"
type: feat
date: 2026-03-02
---

# Strategy Extractor Bot

## Overview

Build a purpose-built system that observes Uncommon-Oat's public trading activity on Polymarket BTC 15-minute prediction markets, extracts their trading strategy as a set of rules across 6 dimensions, and presents the evolving analysis on a live web dashboard. When confidence is high enough, produce a human-readable strategy report and machine-readable ruleset that an independent bot can use — no dependency on watching Oat in real-time.

**This replaces `copycat_bot.py` entirely.** The copycat bot was a copy-trader (mirror Oat's buys). This is fundamentally different — it's a strategy intelligence system.

## Problem Statement

We know Uncommon-Oat is profitable on Polymarket BTC 15-min markets (~$6M/month volume). We don't know **why** they're profitable. Copy-trading doesn't work because:
- Latency: price moves between Oat's fill and our fill
- Scale: Oat's $1,600+/window fills move the market; we'd trade $20-40
- Dependency: if Oat stops, we stop

What we need is to **reverse-engineer the rules** behind their success, then run our own bot independently.

## Proposed Solution

Three-layer architecture + web dashboard:

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│  OAT_OBSERVER │───▶│   OAT_DB     │───▶│  OAT_ANALYZER    │
│  (data collect)│    │  (SQLite)    │    │  (6 dimensions)  │
└──────────────┘    └──────┬───────┘    └────────┬─────────┘
                           │                      │
                    ┌──────▼───────┐    ┌────────▼─────────┐
                    │  SUPABASE    │◀───│ STRATEGY_GENERATOR│
                    │  (cloud DB)  │    │  (report+ruleset) │
                    └──────┬───────┘    └──────────────────┘
                           │
                    ┌──────▼───────┐
                    │  DASHBOARD   │
                    │  (static HTML)│
                    └──────────────┘
```

## Technical Approach

### Architecture

**Observer** (`oat_observer.py`) — Pure data collection. No trading logic. Polls activity API every 3s, snapshots order books every 2s, classifies fills as maker/taker, pushes to SQLite + Supabase. Runs 24/7 on the DigitalOcean server as a systemd service.

**Database** (`oat_db.py`) — Enriched SQLite schema. Stores observations, fills, OB snapshots, and analysis results. Includes `target_wallet` column for future multi-target support.

**Analyzer** (`oat_analyzer.py`) — Runs after each window transition (in-process). Queries SQLite to compute 6 strategy dimensions with confidence scores. Stores results back to SQLite and pushes to Supabase.

**Dashboard** (`oat_dashboard.html`) — Static HTML + Supabase + Chart.js. Shows strategy dimensions evolving in real-time. Follows existing `dashboard.html` pattern.

**Strategy Generator** (`oat_strategy.py`) — On-demand script. When confidence is high enough, produces a markdown strategy report + JSON ruleset.

### The 6 Strategy Dimensions

Each dimension has: **metric**, **analysis method**, **confidence formula**, and **output rule format**.

#### 1. Entry Timing
- **What**: When in the 15-min window does Oat first buy? (seconds after window opens)
- **Metrics**: `first_buy_offset_secs` distribution — median, mean, std_dev, quartiles
- **Correlation**: Does early/late entry correlate with outcomes (UP/DOWN wins)?
- **Confidence**: `min(n_traded_windows / 100, 1.0) × consistency` where consistency = `1 - (std_dev / 450)` (normalized to half-window)
- **Output rule**: `"Enter between T-{X}s and T-{Y}s remaining"`

#### 2. Side Selection
- **What**: Which side does Oat buy first? What market conditions predict the choice?
- **Metrics**: % DOWN-first vs UP-first, grouped by OB imbalance buckets and ask price ranges
- **Analysis**: Conditional probability — P(buy DOWN first | OB imbalance > 0.2)
- **Confidence**: `min(n_first_buys / 100, 1.0) × pattern_strength` where pattern_strength = max conditional probability across condition buckets
- **Output rule**: `"Buy {SIDE} first when {OB_CONDITION}"`

#### 3. Pricing
- **What**: At what price levels does Oat enter? Are they a maker (limit orders) or taker (market orders)?
- **Metrics**: avg entry price per side, spread vs best ask at time of fill, maker/taker ratio
- **Analysis**: Price distribution histograms, maker% over time
- **Confidence**: `min(n_fills / 200, 1.0)` (needs more fills since each window has multiple)
- **Output rule**: `"Target {SIDE} at {X}c (maker ratio: {Y}%)"`

#### 4. Sizing
- **What**: How much does Oat buy per window per side? Fixed or variable?
- **Metrics**: shares per side per window, USDC per window, coefficient of variation
- **Analysis**: Is sizing consistent (low CV) or does it vary with conditions?
- **Confidence**: `min(n_traded_windows / 50, 1.0) × (1 - min(cv, 1.0))`
- **Output rule**: `"Buy {N} shares per side (~${X} per side)"` or `"Size varies: {rule}"`

#### 5. Arb Structure
- **What**: Does Oat always buy both sides? What's the gap between legs? Combined cost?
- **Metrics**: % both-sides windows, leg gap seconds, combined cost, leg order (UP first vs DOWN first)
- **Analysis**: Leg gap distribution, combined cost histogram, which leg comes first in what conditions
- **Confidence**: `min(n_both_sides_windows / 50, 1.0) × arb_consistency` where arb_consistency = % of traded windows that are both-sides
- **Output rule**: `"Arb: buy both sides, combined target < {X}c, ~{Y}s between legs"`

#### 6. Exit Behavior
- **What**: Does Oat ever sell before settlement? Under what conditions?
- **Metrics**: % windows with sells, sell timing, sell price vs entry, conditions at sell time
- **Analysis**: Sell trigger conditions (price drop? time remaining? OB shift?)
- **Confidence**: `min(n_windows_with_sells / 30, 1.0)` (likely low initially — exits are rare)
- **Output rule**: `"Exit when {CONDITION}"` or `"Hold to settlement (no exits observed)"`

### Confidence Scoring

Each dimension produces a score from 0.0 to 1.0:
- **< 0.3**: Not enough data — dimension not shown on dashboard
- **0.3 – 0.6**: Emerging pattern — shown with caveat
- **0.6 – 0.8**: Moderate confidence — pattern is actionable
- **> 0.8**: High confidence — rule is ready for strategy generation

**Overall readiness** = minimum confidence across all 6 dimensions. When overall readiness > 0.6, strategy generation can be triggered.

### SQLite Schema

```sql
-- oat_db.py

CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    window_start INTEGER NOT NULL,
    target_wallet TEXT NOT NULL DEFAULT '0xd0d6053c3c37e727402d84c14069780d360993aa',
    -- Target behavior
    target_traded BOOLEAN DEFAULT FALSE,
    target_sides TEXT,                     -- 'UP', 'DOWN', 'BOTH', 'NONE'
    target_total_buys INTEGER DEFAULT 0,
    target_total_sells INTEGER DEFAULT 0,
    target_up_shares REAL DEFAULT 0,
    target_down_shares REAL DEFAULT 0,
    target_up_avg_price REAL DEFAULT 0,
    target_down_avg_price REAL DEFAULT 0,
    target_up_total_usdc REAL DEFAULT 0,
    target_down_total_usdc REAL DEFAULT 0,
    target_first_buy_offset_secs INTEGER,
    target_first_buy_side TEXT,            -- NEW: which side was bought first
    target_leg_gap_secs INTEGER,           -- NEW: seconds between first and second side
    target_combined_cost REAL,             -- NEW: up_avg + down_avg when both sides
    target_maker_count INTEGER DEFAULT 0,
    target_taker_count INTEGER DEFAULT 0,
    -- Market context at first buy
    up_ask_at_entry REAL,
    down_ask_at_entry REAL,
    ob_imbalance_at_entry REAL,
    time_remaining_at_entry INTEGER,       -- NEW: seconds remaining when first buy
    -- Outcome
    outcome TEXT,                          -- 'UP' or 'DOWN'
    resolved_at INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE target_fills (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL,
    target_wallet TEXT NOT NULL DEFAULT '0xd0d6053c3c37e727402d84c14069780d360993aa',
    tx_hash TEXT UNIQUE NOT NULL,
    timestamp INTEGER NOT NULL,
    side TEXT NOT NULL,                    -- 'BUY' or 'SELL'
    outcome TEXT NOT NULL,                 -- 'Up' or 'Down'
    price REAL NOT NULL,
    size REAL NOT NULL,
    usdc_size REAL NOT NULL,
    fill_type TEXT,                        -- 'MAKER', 'TAKER', 'UNKNOWN'
    ob_snapshot_id INTEGER,
    sequence_in_window INTEGER,            -- NEW: 1st fill, 2nd fill, etc.
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ob_snapshots (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL,
    timestamp REAL NOT NULL,
    up_best_bid REAL,
    up_best_ask REAL,
    up_bid_depth REAL,
    up_ask_depth REAL,
    down_best_bid REAL,
    down_best_ask REAL,
    down_bid_depth REAL,
    down_ask_depth REAL
);

-- NEW: Strategy dimension analysis results
CREATE TABLE analysis_results (
    id INTEGER PRIMARY KEY,
    run_timestamp INTEGER NOT NULL,
    sample_start TEXT,                     -- first slug in sample
    sample_end TEXT,                       -- last slug in sample
    sample_size INTEGER,                   -- number of observations used
    -- Per-dimension confidence scores (0.0 - 1.0)
    entry_timing_confidence REAL DEFAULT 0,
    side_selection_confidence REAL DEFAULT 0,
    pricing_confidence REAL DEFAULT 0,
    sizing_confidence REAL DEFAULT 0,
    arb_structure_confidence REAL DEFAULT 0,
    exit_behavior_confidence REAL DEFAULT 0,
    overall_readiness REAL DEFAULT 0,
    -- Per-dimension extracted values (JSON blobs)
    entry_timing_data JSON,
    side_selection_data JSON,
    pricing_data JSON,
    sizing_data JSON,
    arb_structure_data JSON,
    exit_behavior_data JSON,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_observations_window ON observations(window_start);
CREATE INDEX idx_fills_slug ON target_fills(slug, timestamp);
CREATE INDEX idx_fills_tx ON target_fills(tx_hash);
CREATE INDEX idx_ob_slug ON ob_snapshots(slug, timestamp);
CREATE INDEX idx_analysis_ts ON analysis_results(run_timestamp);
```

### Supabase Tables

Mirror the SQLite schema for dashboard access. The observer pushes data to Supabase in real-time using the existing `supabase_logger.py` pattern:

| Supabase Table | Source | Push Frequency |
|---|---|---|
| `oat_observations` | observations table | At each window end |
| `oat_analysis` | analysis_results table | After each analyzer run |
| `oat_fills` | target_fills table | On each new fill (batched) |

Dashboard reads from these tables. RLS enabled with anon key read-only access.

### Dashboard Components

Single-page static HTML with Chart.js, following `dashboard.html` pattern:

1. **Header**: Strategy Extractor Bot status, overall readiness score (big number), observation count
2. **Dimension Cards** (6 cards, one per dimension):
   - Confidence bar (0-100%)
   - Key metric summary (e.g., "Median entry: T-420s, Oat buys DOWN first 73% of time")
   - Mini chart (distribution histogram or time series)
   - Extracted rule preview when confidence > 0.6
3. **Activity Feed**: Recent Oat fills in real-time
4. **Raw Stats Panel**: Trade frequency, win rate, avg position size, maker/taker ratio
5. **Strategy Report**: Appears when readiness > 0.6, shows full extracted ruleset

### Implementation Phases

#### Phase 1: Observer + Database (Foundation)

Build the data collection layer. Get it running and accumulating data ASAP — every hour of delay is ~4 windows of lost observations.

- [x] Create `oat_db.py` — SQLite schema with all tables, indexes, thread-safe connections
  - Reuse `threading.local()` pattern from `copycat_db.py`
  - Add `target_wallet` column, `sequence_in_window`, `target_first_buy_side`, `target_leg_gap_secs`, `target_combined_cost`, `time_remaining_at_entry`
  - Add `analysis_results` table
  - Add `upsert_observation()`, `insert_fill()`, `insert_ob_snapshot()`, `insert_analysis()`
  - Add `get_observations_for_analysis()` — returns resolved observations with all fields
  - Add `cleanup_old_ob_snapshots(days=7)`

- [x] Create `oat_observer.py` — pure data collection bot
  - BOT_VERSION dict, logging (RotatingFileHandler), Telegram, HTTP session
  - Window detection (`get_current_slug()`)
  - Activity polling (`poll_target_activity()`) — every 3s, dedup by tx_hash
  - OB snapshots (`snapshot_order_book()`) — every 2s, parallel fetch
  - Fill classification (`classify_fill()`) — maker/taker via OB cross-reference
  - Fill processing (`process_fill()`) — store fill, update window state, track sequence
  - Window summarization (`summarize_window()`) — compute aggregates including NEW fields:
    - `target_first_buy_side`: which side was bought first
    - `target_leg_gap_secs`: time between first and second side buys
    - `target_combined_cost`: up_avg + down_avg when both sides bought
    - `time_remaining_at_entry`: 900 - first_buy_offset
  - Background resolution thread for pending outcomes
  - Status line display (console every 1s, log every 15s)
  - **NO paper trading, NO copy-trading** — pure observer

- [x] Create `oat_observer.service` — systemd service file

- [x] Test locally, deploy to server, verify data accumulation
  - Stop `copycat.service` (it's being replaced)
  - Start `oat_observer.service`
  - Verify fills are being stored: `sqlite3 oat.db "SELECT COUNT(*) FROM target_fills"`

**Success criteria**: Observer running on server, accumulating observations + fills + OB snapshots. No errors in log after 1 hour.

#### Phase 2: Analyzer (Strategy Extraction)

Build the analysis engine that computes the 6 strategy dimensions.

- [x] Create `oat_analyzer.py` — strategy dimension analysis
  - `analyze_entry_timing(observations)` → dict with median, mean, std_dev, quartiles, outcome_correlation, confidence
  - `analyze_side_selection(observations)` → dict with first_side_distribution, conditional_probabilities by OB bucket, confidence
  - `analyze_pricing(fills)` → dict with avg_prices, spread_vs_ask, maker_taker_ratio, confidence
  - `analyze_sizing(observations)` → dict with avg_shares, avg_usdc, coefficient_of_variation, confidence
  - `analyze_arb_structure(observations)` → dict with both_sides_rate, leg_gap_distribution, combined_cost_distribution, leg_order, confidence
  - `analyze_exit_behavior(observations, fills)` → dict with sell_rate, sell_conditions, confidence
  - `compute_overall_readiness(dimensions)` → min of all 6 confidence scores
  - `run_analysis()` → orchestrates all 6, stores results to DB

- [ ] Wire analyzer into observer — run after each window transition
  - After `summarize_window()`, call `analyzer.run_analysis()` (every window)
  - Or less frequently: every 10 windows, to reduce compute
  - Store results in `analysis_results` table

- [ ] Add CLI mode: `python3 oat_analyzer.py` runs analysis on-demand and prints report

- [ ] Deploy updated observer with analyzer, verify analysis results accumulating

**Success criteria**: After 50+ observations, analyzer produces non-zero confidence scores for at least entry_timing and arb_structure dimensions.

#### Phase 3: Dashboard (Visualization)

- [x] Create Supabase tables: `oat_observations`, `oat_analysis`, `oat_fills`
  - Add RLS policies (anon read-only)
  - Create `oat_supabase.sql` with table definitions

- [x] Create `oat_supabase.py` — Supabase push layer
  - Follow `supabase_logger.py` pattern (buffered writes, background thread)
  - Push observations at window end
  - Push analysis results after analyzer runs
  - Push fills in batches (every 30s)

- [x] Wire Supabase push into observer

- [x] Create `oat_dashboard.html` — static HTML dashboard
  - Supabase JS client for data fetch
  - Chart.js for visualizations
  - 6 dimension cards with confidence bars + mini charts
  - Overall readiness score
  - Activity feed (recent fills)
  - Raw stats panel
  - Auto-refresh every 60s
  - Dark theme, mobile-friendly (match existing dashboard.html style)

- [x] Deploy dashboard to server, verify it loads and shows data

**Success criteria**: Dashboard shows live strategy insights with updating confidence scores and charts.

#### Phase 4: Strategy Generator (Report + Ruleset)

- [x] Create `oat_strategy.py` — strategy report generator
  - Reads latest `analysis_results` from DB
  - Produces markdown report: human-readable summary of all 6 dimensions with data backing
  - Produces JSON ruleset: machine-readable rules for a trading bot

  ```json
  {
    "version": "1.0",
    "generated_at": "2026-03-15T12:00:00Z",
    "sample_size": 500,
    "overall_readiness": 0.72,
    "rules": {
      "entry_timing": {
        "enter_after_secs": 120,
        "enter_before_secs": 600,
        "confidence": 0.85
      },
      "side_selection": {
        "default_first_side": "DOWN",
        "conditions": [
          {"if": "ob_imbalance > 0.3", "then": "buy DOWN first", "confidence": 0.78}
        ]
      },
      "pricing": {
        "up_target_price": 0.32,
        "down_target_price": 0.65,
        "execution_style": "MAKER",
        "confidence": 0.71
      },
      "sizing": {
        "shares_per_side": 40,
        "confidence": 0.80
      },
      "arb_structure": {
        "both_sides": true,
        "target_combined_cost": 0.93,
        "max_leg_gap_secs": 300,
        "confidence": 0.75
      },
      "exit_behavior": {
        "strategy": "hold_to_settlement",
        "confidence": 0.45
      }
    }
  }
  ```

- [x] Add CLI: `python3 oat_strategy.py` generates report + ruleset
- [x] Add Telegram notification when readiness crosses 0.6 threshold
- [ ] Add strategy report section to dashboard (shown when readiness > 0.6)

**Success criteria**: Running `python3 oat_strategy.py` produces a meaningful strategy report and JSON ruleset that could be fed to a trading bot.

## Files to Create

| File | Purpose | Lines (est) |
|------|---------|-------------|
| `oat_db.py` | SQLite schema + helpers | ~350 |
| `oat_observer.py` | Pure data collection bot | ~600 |
| `oat_observer.service` | systemd service | ~20 |
| `oat_analyzer.py` | 6-dimension strategy analysis | ~500 |
| `oat_supabase.py` | Supabase push layer | ~200 |
| `oat_supabase.sql` | Supabase table definitions | ~80 |
| `oat_dashboard.html` | Static web dashboard | ~800 |
| `oat_strategy.py` | Report + ruleset generator | ~300 |

## Files to Remove/Retire

| File | Action |
|------|--------|
| `copycat_bot.py` | Stop service, keep file for reference |
| `copycat_db.py` | Keep for reference (don't delete data) |
| `copycat.service` | Disable systemd service |

## Acceptance Criteria

### Functional Requirements
- [ ] Observer collects every Oat fill on BTC 15-min markets with zero duplicates
- [ ] OB snapshots taken every 2s with maker/taker classification on every fill
- [ ] Analyzer computes all 6 strategy dimensions with confidence scores
- [ ] Dashboard shows live strategy insights with updating charts
- [ ] Strategy generator produces human-readable report + JSON ruleset
- [ ] Telegram notifications for daily summaries and readiness milestones

### Non-Functional Requirements
- [ ] Observer runs 24/7 without memory leaks or crashes
- [ ] SQLite DB stays under 500MB after months of operation (OB cleanup)
- [ ] Dashboard loads in < 3 seconds
- [ ] No private keys or secrets in any committed file

### Quality Gates
- [ ] Observer runs clean for 24 hours with no errors in log
- [ ] Analyzer produces non-zero confidence for 3+ dimensions after 100 observations
- [ ] Dashboard renders correctly on mobile and desktop

## Dependencies & Prerequisites

- Existing DigitalOcean server (174.138.5.183) with Python 3, SQLite, systemd
- Supabase project (`qszosdrmnoglrkttdevz.supabase.co`) — needs new tables created
- Telegram bot config (`~/.telegram-bot.json`) — already on server
- No new Python packages required (requests, sqlite3, json, math, threading all stdlib)
- Chart.js and Supabase JS loaded from CDN in dashboard HTML

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Activity API doesn't include `slug` field | Medium | High — can't filter by market | Test API response first; fall back to filtering by token IDs |
| Activity API doesn't support backfill | High | Medium — must wait for live data | Accept it; start collecting ASAP |
| Oat changes strategy mid-observation | Low | Medium — confidence scores will drop | Windowed analysis (last N observations) instead of all-time |
| OB snapshot timing mismatch for maker/taker | Always | Low — classification is approximate | Document as "best effort"; still useful as relative metric |
| Supabase rate limits | Low | Low — dashboard just shows stale data | Batch writes, same pattern as existing bot |

## References

- Brainstorm: `docs/brainstorms/2026-03-02-strategy-extractor-bot-brainstorm.md`
- Existing copycat plan: `docs/plans/2026-03-02-feat-copycat-observer-bot-plan.md`
- Reference patterns: `copycat_bot.py` (observer), `copycat_db.py` (SQLite), `dashboard.html` (web), `supabase_logger.py` (Supabase push)
- CLAUDE.md: server details, API endpoints, deployment procedures
