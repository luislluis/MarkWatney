---
title: ROI Check via Polymarket Activity API
type: feat
date: 2026-02-17
---

# ROI Check via Polymarket Activity API

## What We're Building

Replace the Supabase-based ROI calculation in the bot with a direct query to the same Polymarket `/activity` API the dashboard uses. This guarantees the bot's ROI number matches the dashboard exactly — same data source, same win/loss determination.

## Why This Approach

The current Supabase-based ROI check (`get_daily_roi()` in `supabase_logger.py`) has two problems:

1. **Win/loss mismatch**: Bot logs `CAPTURE_99C_LOSS` based on its own detection, but the dashboard uses on-chain redemptions. A hard-stop exit can be logged as LOSS in Supabase while the dashboard shows it as EXIT or even WIN (if the underlying was redeemed).
2. **Data staleness**: Some WIN events have null Shares fields, requiring fallback logic that may not always match.

The dashboard uses a single API call: `GET https://data-api.polymarket.com/activity?user=WALLET&limit=1000`. This returns both TRADE and REDEEM events — the complete picture of on-chain reality.

## Key Decisions

- [x] **Approach A: Query /activity directly** — Single source of truth, ~30 lines of Python, 100% dashboard match
- [ ] ~~Approach B: Hybrid (Supabase + /activity for redemptions only)~~ — Still has formula differences
- [ ] ~~Approach C: Supabase Edge Function~~ — Over-engineered, adds infrastructure
- [x] **Filter to today only** — Midnight EST, matches dashboard's "today's ROI"
- [x] **Replace `get_daily_roi()` entirely** — No more Supabase-based ROI calc

## How It Works

1. Bot calls `/activity?user=WALLET&limit=1000`
2. Filters TRADE events to today (>= midnight EST)
3. Builds `redeemed` set from REDEEM events (by slug)
4. Groups trades by slug+side, matches sells to buys (FIFO)
5. Classifies: EXIT (sold before expiry), WIN (redeemed), LOSS (>30min, not redeemed)
6. Calculates: `ROI = totalPnl / avgTradeValue` where `avgTradeValue = totalCost / numWindows`
7. If ROI >= 45% → halt trading

## What's Already in Place

- Bot has `requests`, `http_session`, `WALLET_ADDRESS`
- Bot already calls `/positions` and `/trades` endpoints
- The `/activity` endpoint requires no authentication (public data)
- Dashboard code (dashboard.html lines 900-1107) has the exact logic to replicate

## Open Questions

- Should we cache the /activity response to avoid hitting the API too often? (Probably yes — check every window boundary, ~15 min)
- Do we keep the Supabase `get_daily_roi()` as a fallback if /activity is down?
