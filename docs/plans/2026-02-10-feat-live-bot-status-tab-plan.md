---
title: "feat: Add Live Bot Status Tab to Dashboard"
type: feat
date: 2026-02-10
---

# feat: Add Live Bot Status Tab to Dashboard

## Overview

Add a "Live Status" tab to `dashboard.html` that shows real-time bot activity: current state badge, window countdown timer, market prices, positions, BTC price, danger score, a mini price chart for the current window, and a scrolling event feed. Uses Supabase Realtime subscriptions on the existing Ticks and Events tables — zero bot changes needed.

## Problem Statement / Motivation

Currently the only way to see what the bot is doing right now is to SSH into the server and `tail -f` the log file. The dashboard shows trade outcomes (wins/losses/exits) but not the live trading process. A visual status panel would let the user monitor the bot from anywhere without SSH access.

## Proposed Solution

### Phase 1: Supabase Migration (Prerequisite)

The Ticks table (`Polymarket Bot Log - Ticks`) is **not** currently in the Supabase Realtime publication and has **no RLS policy** for anonymous reads. This must be fixed before the dashboard can subscribe to tick updates.

**Migration: `enable_ticks_realtime`**

```sql
-- Add RLS policy for anonymous reads on Ticks table (matching Events table pattern)
CREATE POLICY "Allow anonymous reads"
  ON "Polymarket Bot Log - Ticks"
  FOR SELECT
  TO anon
  USING (true);

-- Add Ticks table to the realtime publication
ALTER PUBLICATION supabase_realtime
  ADD TABLE "Polymarket Bot Log - Ticks";
```

### Phase 2: Tab Navigation System

Add a tab bar above the main content area in `dashboard.html`. Two pill-shaped buttons: **Trades** (default) and **Live Status**.

**Behavior:**
- Toggle visibility of two container divs (`#trades-view` and `#live-view`)
- Support hash routing: `#live` opens Live Status, default opens Trades
- Active tab gets green accent (`--green-primary`), inactive gets muted style
- Trades tab remains default on load (preserves existing behavior)

**Implementation in `dashboard.html`:**

```html
<!-- Tab bar (after header, before summary grid) -->
<div class="tab-bar">
  <button class="tab active" data-tab="trades">Trades</button>
  <button class="tab" data-tab="live">Live Status</button>
</div>

<!-- Wrap existing content -->
<div id="trades-view"> ... existing dashboard content ... </div>

<!-- New live status view -->
<div id="live-view" style="display:none"> ... new content ... </div>
```

```css
.tab-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.tab {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  padding: 6px 16px;
  border-radius: 8px;
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: all 0.2s;
}
.tab.active {
  color: var(--green-primary);
  border-color: var(--green-primary);
  background: var(--green-glow);
}
```

### Phase 3: Status Panel

A card at the top of the Live Status view showing the bot's current state.

**Layout:**

```
┌─────────────────────────────────────┐
│  ● IDLE          T-534s    Last: 2s │
│                                     │
│  UP  43¢        DN  58¢             │
│  pos: 0         pos: 0              │
│                                     │
│  BTC $68,806    Danger: --          │
│  Reason: no diverge (43c>42c)       │
└─────────────────────────────────────┘
```

**Status badge colors:**

| Status | Color | Meaning |
|--------|-------|---------|
| IDLE | `--text-muted` (gray) | Waiting for opportunity |
| SNIPER | `--green-primary` (green) | 99c capture active |
| PAIRED | `--green-primary` (green) | Both legs filled, waiting |
| PAIRING | `--yellow-primary` (yellow) | One leg filled, completing |
| IMBAL | `--red-primary` (red) | Emergency imbalance |
| EXITED | `--text-secondary` (dim) | Position closed |
| Unknown | `--text-muted` (gray) | Fallback for future statuses |

**Staleness indicator:** "Last: Xs" in the top-right corner of the status card.
- Green text when < 60s since last tick
- Yellow (`--yellow-primary`) when 60s-300s
- Red (`--red-primary`) when > 300s (bot likely crashed)

**Danger score display:**
- Show "--" when NULL (most of the time when IDLE)
- Green text when < 0.20
- Yellow when 0.20-0.39
- Red when >= 0.40 (hedge threshold)

**Countdown timer:**
- Displays `T-XXXs` using TTL from latest tick
- Interpolates locally: decrements every second via `setInterval`
- Snaps to actual TTL when a new tick batch arrives (may jump 1-2s, acceptable)
- Shows "WAIT" when between windows (no current window)

### Phase 4: Mini Price Chart

A Chart.js line chart showing UP and DN ask prices over the current 15-minute window.

**Data source:** Query Ticks table for current window on tab open, then append from Realtime.

**Initial load query:**
```sql
SELECT "Timestamp", "UP Ask", "DN Ask"
FROM "Polymarket Bot Log - Ticks"
WHERE "Window ID" = $currentWindowId
ORDER BY "Timestamp" ASC
```

**Chart config:**
- Two lines: UP Ask (green `#34d399`) and DN Ask (red `#f87171`)
- Y-axis: 0¢ to 100¢ (or auto-scale to data range with padding)
- X-axis: time labels (HH:MM:SS), JetBrains Mono 10px
- Dark theme matching existing balance chart
- Downsample to 1 point per 5 seconds (~180 points max) for performance
- Follow existing update-or-create pattern from `updateBalanceChart()`

**Window transitions:**
- When Window ID changes in incoming ticks, clear chart data arrays and restart
- No animation needed — clean reset

**v1 scope:** UP/DN ask prices only. Order book imbalance overlay deferred to v2.

### Phase 5: Event Feed

A scrolling list of recent trading events below the chart.

**Display:** Last 25 events, newest at top, auto-scroll on new events.

**Initial load query:**
```sql
SELECT *
FROM "Polymarket Bot Log - Events"
ORDER BY "Timestamp" DESC
LIMIT 25
```

**Event rendering (per event type):**

| Event | Icon | Color | Display Format |
|-------|------|-------|----------------|
| WINDOW_START | ⏱ | muted | "New window: {Window ID}" |
| CAPTURE_99C | 🎯 | green | "99c snipe placed: {Side} @ {Price}¢" |
| CAPTURE_FILL | ✅ | green | "Filled: {Shares} {Side} @ {Price}¢" |
| CAPTURE_99C_WIN | 🏆 | green | "WIN +${PnL}" |
| CAPTURE_99C_LOSS | ❌ | red | "LOSS -${PnL}" |
| 99C_EARLY_EXIT | 🚪 | yellow | "Early exit: {Side}" |
| HARD_STOP_EXIT | 🛑 | red | "Hard stop exit: {Side}" |
| ARB_ORDER | 📋 | muted | "ARB order: {Side} @ {Price}¢" |
| ARB_FILL | ✅ | green | "ARB filled: {Shares} {Side}" |
| PROFIT_PAIR | 💰 | green | "Profit pair +${PnL}" |
| BALANCE_SNAPSHOT | 💰 | muted | "Balance: {details}" |
| ERROR | ⚠️ | red | "Error: {details}" |
| Other | 📌 | muted | "{Event}: {details}" |

**Timestamp format:** `HH:MM:SS` in ET timezone, monospace.

### Phase 6: Realtime Subscription

**Ticks subscription** (new, on same Supabase client):

```javascript
// New channel for live status data
liveChannel = db
  .channel('polybot-live-status')
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'Polymarket Bot Log - Ticks'
  }, (payload) => {
    // Update status panel with latest tick
    // Append to chart data (downsampled)
    // Snap countdown timer to new TTL
  })
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'Polymarket Bot Log - Events'
  }, (payload) => {
    // Prepend event to feed
  })
  .subscribe(handleSubscriptionStatus);
```

**Subscription lifecycle:**
- Only subscribe when Live Status tab is active
- Unsubscribe when switching to Trades tab (reduces unnecessary traffic)
- Re-subscribe when switching back
- Handle tab visibility (existing pattern: stop on hidden, reconnect on visible)

**Fallback:** If Realtime fails, poll latest tick every 10 seconds (same error handling pattern as existing Events subscription).

## Technical Considerations

- **All Ticks numeric columns are stored as `text`** — must `parseFloat()` every value on the client
- **Ticks have ~30s flush latency** — the timer interpolates locally between updates
- **Ticks volume:** ~30 rows per batch. Client processes all but only displays the latest for the status panel; downsamples for the chart
- **Single HTML file** — all changes go into `dashboard.html` (CSS, HTML, JS)
- **No new dependencies** — uses existing Supabase JS v2 and Chart.js v4

## Acceptance Criteria

- [x] **Migration**: Ticks table has RLS anon-read policy and is in `supabase_realtime` publication
- [x] **Tab bar**: Two tabs (Trades / Live Status) toggle content visibility
- [x] **Status panel**: Shows current status badge, countdown timer, UP/DN prices, positions, BTC price, danger score, reason
- [x] **Staleness indicator**: Shows time since last tick; changes color at 90s (yellow) and 300s (red)
- [x] **Countdown timer**: Shows TTL adjusted for flush latency (receivedTTL - tickAge)
- [x] **Mini chart**: Shows UP/DN ask prices for current window; resets on window transition
- [x] **Event feed**: Shows last 25 events with color-coded icons; updates in near-real-time
- [x] **Mobile**: Components use existing 420px container, single-column layout
- [x] **Realtime**: Single subscription on page load, handles both Ticks + Events

## Dependencies & Risks

| Risk | Mitigation |
|------|------------|
| Ticks Realtime publication not enabled | Phase 1 migration handles this |
| High tick volume on Realtime channel | Client only uses latest tick; chart downsamples |
| Bot offline = stale data | Staleness indicator turns yellow/red |
| Window transition flash | Clean chart reset, no animation |

## References

- Brainstorm: `docs/brainstorms/2026-02-10-live-status-tab-brainstorm.md`
- Existing dashboard: `dashboard.html` (lines 1068-1120 for Realtime pattern, lines 630-706 for Chart.js pattern)
- Ticks table: `Polymarket Bot Log - Ticks` (~704K rows, per-second data)
- Events table: `Polymarket Bot Log - Events` (~3.6K rows, 16 event types)
- Supabase views: `supabase_views.sql`
