# Live Bot Status Tab - Brainstorm

**Date:** 2026-02-10
**Status:** Ready for planning

## What We're Building

A new **Live Status** tab on the dashboard (alongside the existing Trades tab) that shows the bot's current state in real time. This is a visual version of the bot's per-second log line, enriched with a mini price chart and scrolling event feed.

### Components

1. **Status Panel (top)**
   - Status badge: IDLE / SNIPER / PAIRED / PAIRING / IMBAL / RISK
   - Window countdown timer (T-XXXs, interpolated locally between updates)
   - UP/DN ask prices
   - Current position (UP shares / DN shares)
   - Idle reason (e.g., "no diverge (43c>42c)")
   - BTC price
   - Danger score

2. **Mini Price Chart (middle)**
   - UP/DN ask prices over the current 15-minute window
   - Uses last ~15 minutes of Ticks data
   - Resets when a new window starts

3. **Event Feed (bottom)**
   - Scrolling log of recent trading events (fills, exits, wins, losses, errors)
   - Near-instant updates via Supabase realtime on Events table

## Why This Approach

**Supabase Realtime subscription** (Approach A) was chosen over polling because:

- The dashboard already uses Supabase realtime for the Events table — same pattern, same client
- Zero bot changes needed — Ticks table already receives per-second data (flushed every ~30s)
- Events are written immediately (near-instant), Ticks have ~30s latency (acceptable)
- Countdown timer interpolates locally between tick updates for smooth UX

## Data Sources

| Data | Source | Latency | Already Exists? |
|------|--------|---------|-----------------|
| Status, TTL, prices, positions, reason | `Polymarket Bot Log - Ticks` table | ~30s (batch flush) | Yes (703K+ rows) |
| BTC price, danger score | Same Ticks table | ~30s | Yes |
| Trading events (fills, wins, exits) | `Polymarket Bot Log - Events` table | ~1-2s (immediate write) | Yes (3.6K+ rows) |

## Key Decisions

1. **New tab, not inline** — Clean separation from trade analytics
2. **Supabase Realtime, not polling** — Reuses existing infrastructure
3. **Local countdown interpolation** — Decrement TTL every second between tick updates for smooth timer
4. **No bot changes** — Everything uses existing data pipelines

## Open Questions

- Should the mini chart show order book imbalance overlay? (Ticks table has `up_imb`/`down_imb`)
- How many events to show in the feed? (Last 20? Last hour?)
- Should the status panel show when the last tick was received (staleness indicator)?
