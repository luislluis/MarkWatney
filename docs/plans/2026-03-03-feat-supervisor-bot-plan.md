---
title: "feat: Supervisor Bot — Independent Trading Analyst"
type: feat
date: 2026-03-03
brainstorm: docs/brainstorms/2026-03-03-supervisor-bot-brainstorm.md
---

# feat: Supervisor Bot — Independent Trading Analyst

## Overview

Build a standalone Python supervisor bot (`supervisor_bot.py`) that runs as its own systemd service alongside the trading bot. It independently monitors every 15-minute window by:

1. **Tailing the bot log** (`~/polybot/bot.log`) for the bot's perspective — what it claims happened
2. **Querying Polymarket APIs** for ground truth — what actually happened
3. **Comparing the two**, classifying each window, diagnosing failures, and writing audit results to Supabase

A new **Watchdog tab** on `arb-dashboard.html` displays: scorecard summary (pair rate, P&L, issue counts by type as # and %) and a chronological timeline of per-window audit cards with diagnosis details.

## Problem Statement / Motivation

The arb bot places both sides of a BTC Up/Down market to lock in arbitrage profit. The #1 pain point: **one leg fills but the pair fails**, leaving a stranded position that loses money on rescue. Today there's no independent system to:

- Verify the bot's claims against Polymarket's actual records
- Track how often pairing fails and why
- Identify patterns in what causes wins vs losses
- Surface actionable recommendations to improve the bot's strategy

The supervisor solves this by being a completely separate process that doesn't trust the bot — it verifies everything.

## Proposed Solution

### Architecture

```
polybot-supervisor.service (new)
    ├── Tails ~/polybot/bot.log (real-time, line by line)
    ├── Queries Polymarket APIs (positions, activity, order book)
    ├── Compares bot claims vs API ground truth
    ├── Writes audit results to Supabase
    └── Logs to ~/polybot/supervisor.log

arb-dashboard.html (modified)
    ├── [Existing] Trades tab — arb trades from Activity API
    └── [New] Watchdog tab — reads from Supabase audit tables
```

### Key Architecture Decisions

**Bot log as primary source for bot perspective.** The bot log contains real-time state (status, prices, danger scores, OB depth) that the Supabase Events table doesn't capture at the same granularity. The user explicitly wants the log as the bot's "testimony." The supervisor parses structured status lines + event markers from the log.

**Polymarket API as ground truth.** The supervisor independently queries:
- `data-api.polymarket.com/positions` — actual positions held
- `data-api.polymarket.com/activity` — actual trade history with fill prices
- `gamma-api.polymarket.com/events` — market metadata and resolution
- `clob.polymarket.com/book` — order book depth (during window, for diagnosis)

**API staleness protection.** Wait 45 seconds after window end before final verification query. Retry up to 3 times at 15-second intervals if positions API returns 0 when the log showed fills. This mirrors the bot's own `max(local, api)` lesson.

**Strategy-aware classification.** The supervisor reads the bot's startup banner to determine which strategy is active (`ARB_ENABLED` vs 99c-only). Classification logic adapts accordingly:
- **ARB mode**: Track pair rate, UNPAIRED failures, bail/rescue outcomes
- **99c mode**: Track snipe rate, win/loss, exit types (profit lock, hard stop, danger exit)

## Technical Approach

### Phase 1: Supervisor Core + Supabase Schema

Build the supervisor bot itself and the Supabase tables it writes to.

#### New Files

**`supervisor_bot.py`** — Main supervisor script

**`polybot-supervisor.service`** — Systemd unit file (mirrors `polybot.service` pattern)

#### Supabase Table: `Supervisor - Window Audits`

| Column | Type | Description |
|--------|------|-------------|
| `id` | `uuid` (auto) | Primary key |
| `window_id` | `text` (unique) | Slug: `btc-updown-15m-{ts}` |
| `audit_timestamp` | `timestamptz` | When supervisor wrote this audit |
| `window_start` | `timestamptz` | Window start time |
| `classification` | `text` | See classification taxonomy below |
| `severity` | `text` | `ok`, `medium`, `high`, `critical` |
| `strategy_mode` | `text` | `arb` or `99c_sniper` |
| `bot_status` | `text` | Final status from log: `PAIRED`, `IDLE`, `SNIPER`, etc. |
| `bot_up_shares` | `numeric` | UP shares bot claimed (from log) |
| `bot_down_shares` | `numeric` | DOWN shares bot claimed (from log) |
| `api_up_shares` | `numeric` | UP shares per Polymarket API |
| `api_down_shares` | `numeric` | DOWN shares per Polymarket API |
| `bot_fill_price` | `numeric` | Fill price bot logged |
| `api_fill_price` | `numeric` | Fill price from activity API |
| `pnl_bot` | `numeric` | P&L the bot reported |
| `pnl_verified` | `numeric` | P&L calculated from API data |
| `entry_confidence` | `numeric` | Confidence at entry (from log, 99c mode) |
| `entry_ttl` | `integer` | Seconds remaining at entry |
| `exit_type` | `text` | How position closed: `settlement`, `profit_lock`, `hard_stop`, `danger_exit`, `bail`, `rescue` |
| `exit_price` | `numeric` | Price at exit (if early exit) |
| `market_outcome` | `text` | `UP`, `DOWN`, or `null` (pending) |
| `ob_depth_at_entry` | `numeric` | Order book depth when bot entered (from log) |
| `diagnosis` | `text` | Root cause explanation |
| `recommendation` | `text` | Actionable suggestion |
| `observation_complete` | `boolean` | Was full window observed? |
| `api_verified` | `boolean` | Was API data fresh (not stale)? |
| `details` | `jsonb` | Raw data blob for debugging |
| `created_at` | `timestamptz` | Auto timestamp |

#### Supabase Table: `Supervisor - Daily Summary`

| Column | Type | Description |
|--------|------|-------------|
| `id` | `uuid` (auto) | Primary key |
| `date` | `date` (unique) | EST date |
| `total_windows` | `integer` | Windows the bot was active |
| `idle_windows` | `integer` | Windows bot chose not to trade |
| `traded_windows` | `integer` | Windows where bot entered a position |
| `clean_wins` | `integer` | Clean settlements or profit locks |
| `unpaired` | `integer` | One leg filled, pair failed |
| `bails` | `integer` | Forced bail from imbalance |
| `hard_stops` | `integer` | Emergency exits |
| `danger_exits` | `integer` | Danger score triggered exits |
| `profit_locks` | `integer` | Successful profit lock sells |
| `pnl_verified` | `numeric` | Total P&L from API data |
| `pnl_bot_reported` | `numeric` | Total P&L bot claimed |
| `pair_rate` | `numeric` | % of arb windows where both legs filled |
| `win_rate` | `numeric` | % of traded windows that were profitable |
| `pattern_analysis` | `text` | Daily pattern summary |
| `recommendations` | `text` | Daily recommendations |
| `created_at` | `timestamptz` | Auto timestamp |

#### Classification Taxonomy

**ARB mode classifications:**

| Classification | Severity | Trigger |
|----------------|----------|---------|
| `ARB_PAIRED_WIN` | ok | Both legs filled, settled profitably |
| `ARB_PAIRED_LOSS` | medium | Both legs filled, but combined > $1.00 |
| `UNPAIRED_BAIL` | critical | One leg filled, pair failed, bot bailed |
| `UNPAIRED_RESCUE` | critical | One leg filled, had to rescue-sell at a loss |
| `HARD_STOP` | high | Emergency bid-collapse exit |
| `POSITION_MISMATCH` | medium | Bot position count != API count |
| `PRICE_DISCREPANCY` | medium | Bot fill price != API fill price (>1c diff) |
| `IDLE` | ok | Bot chose not to trade (no divergence) |
| `TREND_BLOCKED` | ok | Entry blocked by trend filter |
| `ROI_HALTED` | ok | Trading halted (daily ROI target met) |

**99c sniper mode classifications:**

| Classification | Severity | Trigger |
|----------------|----------|---------|
| `SNIPE_WIN` | ok | 99c capture, settled at $1.00 |
| `SNIPE_PROFIT_LOCK` | ok | 99c capture, sold at 99c before settlement |
| `SNIPE_LOSS` | high | 99c capture, lost at settlement |
| `SNIPE_HARD_STOP` | high | 99c capture, hard stop exit |
| `SNIPE_DANGER_EXIT` | medium | 99c capture, danger score exit |
| `PROFIT_LOCK_MISS` | medium | Fill confirmed but profit lock sell didn't fill |
| `POSITION_MISMATCH` | medium | Bot vs API position disagreement |
| `PRICE_DISCREPANCY` | medium | Fill price mismatch |
| `IDLE` | ok | Not trading this window |
| `TREND_BLOCKED` | ok | Entry blocked by trend filter |
| `ROI_HALTED` | ok | Daily ROI target met |

#### Supervisor Main Loop

```
supervisor_bot.py pseudocode:

1. Startup:
   - Load credentials from ~/.env
   - Connect to Supabase
   - Open bot.log for tailing
   - Detect current strategy mode from startup banner
   - Log startup to ~/polybot/supervisor.log

2. Main loop (runs continuously):
   a. Read new lines from bot.log
   b. Parse each line:
      - Status lines: extract status, TTL, prices, positions, danger score
      - Event lines: extract orders placed, fills detected, exits
      - Window transitions: detect "NEW WINDOW" or slug change
   c. On window transition:
      - Finalize previous window's bot-side data
      - Wait 45 seconds for API settlement
      - Query Polymarket APIs for ground truth
      - Compare bot claims vs API reality
      - Classify the window
      - Generate diagnosis (if issue found)
      - Write audit row to Supabase
      - Update daily summary
   d. Every 30 seconds:
      - Check log freshness (last line timestamp)
      - If stale > 30s AND not near window boundary: flag BOT_DOWN
   e. At midnight EST (or on first window after midnight):
      - Finalize previous day's summary
      - Run daily pattern analysis
      - Write daily summary to Supabase
```

#### Log Parsing Strategy

Parse two categories of log lines:

**1. Status lines** (every second):
```
[HH:MM:SS] STATUS  | T-XXXs | ... | pos:X/X | reason
```
Regex: `\[(\d{2}:\d{2}:\d{2})\]\s+(\w+)\s+\|\s+T-(\d+)s\s+\|.*\|\s+pos:(\d+\.?\d*)/(\d+\.?\d*)\s+\|\s+(.+)`

Extract: timestamp, status, TTL, up_shares, down_shares, reason

**2. Event markers** (key strings to watch for):
- `"ARB_ORDER"` or `"placed"` + `"order"` — order placed
- `"ORDER_FILL_DETECTED"` — fill confirmed
- `"CAPTURE_FILL"` or `"99c CAPTURE FILLED"` — 99c fill
- `"PROFIT_LOCK"` — profit lock activity
- `"HARD_STOP"` — hard stop triggered
- `"DANGER_EXIT"` — danger exit
- `"WINDOW COMPLETE"` — window ended
- `"POLYBOT"` + `"starting"` — bot restart
- `"PAIRING_MODE"` or `"PAIRING"` — entered pairing state
- `"BAIL"` or `"bail"` — bail triggered
- `"TRADING HALTED"` — ROI halt
- `"TREND_GUARD_BLOCK"` — trend filter blocked entry

#### Diagnosis Engine (v1 — Pattern Matching)

For each non-OK classification, the supervisor runs simple pattern matching:

| Issue | Diagnosis Logic | Example Output |
|-------|----------------|----------------|
| `UNPAIRED_BAIL` | Check: was second leg ever placed? If yes, did it expire? What was book depth? | "DOWN order placed at 41c but only $30 available at that level. Order expired after 12s. Book was too thin." |
| `HARD_STOP` | Check: what triggered it? bid collapse speed? BTC price move? | "Best bid dropped from 94c to 42c in 8 seconds. BTC moved -$180 in that window. Hard stop fired at 44c, filled at ~32c." |
| `SNIPE_LOSS` | Check: entry confidence, time remaining, what changed? | "Entered at 96% confidence (97c ask, T-90s, 3% penalty). Market reversed in final 30s. BTC dropped $120." |
| `PRICE_DISCREPANCY` | Compare bot price vs API price, calculate delta | "Bot logged fill at 95c but API shows 97c. Delta: 2c ($0.50 on 25 shares)." |
| `PROFIT_LOCK_MISS` | Check: was sell order placed? Did it get cancelled? | "Profit lock sell placed at 99c. Best bid dropped below 60c → sell cancelled. Hard stop took over." |

#### Bot Liveness Check

- Track timestamp of last parsed log line
- If no new lines for 30 seconds AND current time is not within 30s of a window boundary (where quiet periods are normal): write `BOT_DOWN` event
- On detecting startup banner after a gap: write `BOT_RESTART` event with downtime duration
- Exempt first 60 seconds after supervisor startup from BOT_DOWN detection (supervisor may be catching up on log)

#### Supervisor Startup Recovery

When the supervisor starts (or restarts after a crash):
1. Check current window: `window_start = (int(time.time()) // 900) * 900`
2. Query Supabase for the last audit row written
3. If last audit is for a previous window: mark current window as `observation_complete = False`
4. Begin tailing the log from current position (don't replay old log)
5. The first window will be a partial observation — this is fine

### Phase 2: Watchdog Tab on arb-dashboard.html

Add a tab system to `arb-dashboard.html` and build the Watchdog view.

#### Tab Navigation

Add two tabs: **Trades** (existing content) and **Watchdog** (new).

Follow the exact pattern from `dashboard.html`:
- CSS: `.tab-bar`, `.tab`, `.tab.active` classes
- HTML: `<div class="tab-bar">` with buttons
- JS: `switchTab()` function that shows/hides `<div id="trades-view">` and `<div id="watchdog-view">`
- Lazy loading: Watchdog data only fetches when tab is first activated

#### Watchdog Scorecard (Top Section)

Three stat cards matching existing design:

| Card | Value | Detail |
|------|-------|--------|
| **Pair Rate** (arb mode) or **Win Rate** (99c mode) | `XX%` | `X paired / Y traded` or `X wins / Y trades` |
| **Verified P&L** | `+$X.XX` or `-$X.XX` | Difference from bot-reported: `(+$0.12 vs bot)` |
| **Issues** | `X` (count) | `X critical · Y high · Z medium` |

#### Issue Breakdown Bar

Below the scorecard, a horizontal bar showing issue type distribution:

```
[██████████ CLEAN 70% ][████ UNPAIRED 20% ][██ HARD_STOP 10% ]
```

Each segment colored by severity (green=ok, red=critical, orange=high, yellow=medium). Below the bar, a legend with counts:

```
CLEAN: 7 (70%)  |  UNPAIRED: 2 (20%)  |  HARD_STOP: 1 (10%)
```

#### Per-Window Audit Timeline

Below the scorecard, a chronological list of audit cards (most recent first). Same card styling as the existing trade list.

**Audit card layout:**

```
[Icon] [Classification]           [Verified P&L]
       [Window time] · [Strategy]
       [Diagnosis text if issue]
       [Recommendation if issue]
```

- Icon: green checkmark (ok), red X (critical), orange warning (high), yellow dot (medium)
- Classification shown as badge (e.g., `ARB_PAIRED_WIN`, `UNPAIRED_BAIL`)
- Diagnosis and recommendation only shown for non-OK classifications
- IDLE windows shown as collapsed/minimal entries (just time + "idle" label)
- Expandable details on tap/click for full audit data

#### Data Fetching

```javascript
// Add Supabase client to arb-dashboard.html
const db = window.supabase.createClient(CONFIG.supabaseUrl, CONFIG.supabaseAnonKey);

// Fetch today's audits
const { data: audits } = await db
  .from('Supervisor - Window Audits')
  .select('*')
  .gte('window_start', todayMidnightEST)
  .order('window_start', { ascending: false });

// Fetch daily summary
const { data: summary } = await db
  .from('Supervisor - Daily Summary')
  .select('*')
  .order('date', { ascending: false })
  .limit(7);
```

Refresh: Poll every 30 seconds (matching existing `CONFIG.refreshInterval`). Optionally use Supabase Realtime subscription for instant updates on new audit rows.

### Phase 3: Deployment & Service Setup

#### `polybot-supervisor.service`

```ini
[Unit]
Description=Polymarket Supervisor Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/polymarket_bot
EnvironmentFile=/root/.env
ExecStart=/usr/bin/python3 /root/polymarket_bot/supervisor_bot.py
Restart=always
RestartSec=15
StandardOutput=append:/root/polybot/supervisor.log
StandardError=append:/root/polybot/supervisor.log

[Install]
WantedBy=multi-user.target
```

#### Deployment Steps

1. `scp supervisor_bot.py polybot-supervisor.service root@174.138.5.183:~/polymarket_bot/`
2. SSH into server
3. `cp ~/polymarket_bot/polybot-supervisor.service /etc/systemd/system/`
4. `systemctl daemon-reload`
5. `systemctl enable polybot-supervisor.service`
6. `systemctl start polybot-supervisor.service`
7. `tail -20 ~/polybot/supervisor.log` to verify

#### Supabase Table Creation

Run in Supabase SQL editor (`https://supabase.com/dashboard/project/qszosdrmnoglrkttdevz/sql`):

```sql
-- Window Audits table
CREATE TABLE "Supervisor - Window Audits" (
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

-- Enable RLS but allow anon reads (dashboard)
ALTER TABLE "Supervisor - Window Audits" ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow anon read" ON "Supervisor - Window Audits"
  FOR SELECT USING (true);
CREATE POLICY "Allow service write" ON "Supervisor - Window Audits"
  FOR ALL USING (true);

-- Enable Realtime
ALTER publication supabase_realtime ADD TABLE "Supervisor - Window Audits";

-- Daily Summary table
CREATE TABLE "Supervisor - Daily Summary" (
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
```

## Acceptance Criteria

### Functional Requirements

- [x] `supervisor_bot.py` runs as a standalone systemd service, independent from the trading bot
- [x] Tails `~/polybot/bot.log` in real-time and parses status lines + event markers
- [x] Queries Polymarket position/activity APIs after each window to verify bot claims
- [x] Classifies each window using the taxonomy (strategy-aware: arb vs 99c)
- [x] Generates diagnosis text for non-OK classifications explaining root cause
- [x] Generates recommendation text for non-OK classifications
- [x] Writes audit results to `Supervisor - Window Audits` Supabase table
- [x] Updates `Supervisor - Daily Summary` at end of each window and at midnight EST
- [x] Detects bot crashes (log staleness > 30s) and writes BOT_DOWN event
- [x] Detects bot restarts (startup banner) and writes BOT_RESTART event
- [x] Handles API staleness: waits 45s post-window, retries 3x at 15s intervals
- [x] Handles mid-window startup gracefully (marks as `observation_complete = False`)
- [x] `arb-dashboard.html` has Trades and Watchdog tabs
- [x] Watchdog scorecard shows: pair/win rate, verified P&L, issue count with severity breakdown
- [x] Watchdog shows issue type distribution bar (count and % per type)
- [x] Watchdog timeline shows per-window audit cards with classification, diagnosis, recommendation
- [x] IDLE windows shown as minimal/collapsed entries in timeline
- [x] Dashboard reads from Supabase, refreshes every 30 seconds

### Non-Functional Requirements

- [x] Supervisor does NOT place orders or modify bot state (read-only watchdog)
- [x] Supervisor logs to its own file: `~/polybot/supervisor.log`
- [x] Supervisor follows BOT_VERSION convention per CLAUDE.md
- [x] Auto-restarts via systemd on crash (`Restart=always`)
- [x] Does not exceed Polymarket API rate limits (max 2 API calls per window end)

## Dependencies & Risks

| Dependency | Risk | Mitigation |
|------------|------|------------|
| Bot log format | Cosmetic changes to log break parsing | Use broad regex patterns, fail gracefully on parse errors |
| Polymarket API availability | API down = no ground truth | Mark window as `api_verified = False`, use log data only |
| Supabase availability | Can't write audits | Buffer locally, retry on reconnect |
| Bot strategy mode | Wrong mode = wrong classifications | Read startup banner, default to detecting mode from position patterns |
| API staleness | False UNPAIRED from stale 0/0 | 45s wait + 3 retries + staleness flag |

## Success Metrics

- Supervisor catches 100% of UNPAIRED events (no stranded position goes unnoticed)
- Issue type breakdown gives clear signal on what % of windows are failing and why
- Verified P&L matches bot-reported P&L within 1% on clean windows
- Dashboard Watchdog tab loads and displays data within 3 seconds
- Supervisor introduces zero impact on trading bot performance (completely separate process)

## References

### Internal
- Brainstorm: `docs/brainstorms/2026-03-03-supervisor-bot-brainstorm.md`
- Existing supervisor skeleton: `performance_tracker.py`
- Supabase logger pattern: `supabase_logger.py`
- Systemd service pattern: `polybot.service`
- Dashboard tab pattern: `dashboard.html` (lines 578-607 CSS, lines 774-778 HTML, lines 1978-1999 JS)
- Bot log format: `trading_bot_smart.py` (line 1262 `log_state()`)
- Bot event taxonomy: `trading_bot_smart.py` (all `log_event()` calls)

### API Endpoints
- Positions: `https://data-api.polymarket.com/positions?user={wallet}`
- Activity: `https://data-api.polymarket.com/activity?user={wallet}&limit=1000`
- Market data: `https://gamma-api.polymarket.com/events?slug={slug}`
- Order book: `https://clob.polymarket.com/book?token_id={id}`
- Resolution: `https://clob.polymarket.com/markets/{condition_id}`
