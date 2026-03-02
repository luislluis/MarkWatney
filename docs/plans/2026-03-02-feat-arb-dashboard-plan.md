---
title: Arbitrage Dashboard for Maker ARB Bot
type: feat
date: 2026-03-02
---

# feat: Arbitrage Dashboard for Maker ARB Bot

## Overview

Create `arb-dashboard.html` — a standalone GitHub Pages dashboard for the maker arb bot. Shows arbitrage pair detection, profit tracking, win rates, and daily trade history using the same dark terminal design as the existing dashboard.

## Implementation Plan

### Phase 1: Create `arb-dashboard.html`

- [x] Scaffold HTML with same design system (CSS variables, fonts, background mesh, noise texture)
- [x] Header: "POLYBOT ARB" with ⚖️ icon
- [x] Top stat cards: Today's Arb P&L, Win Rate, Total Arb Locked
- [x] Trade list section with date separators
- [x] Daily history sidebar

### Phase 2: Data Fetching & Arb Detection

- [x] Fetch from `data-api.polymarket.com/activity?user=WALLET&limit=1000`
- [x] Filter arb bot trades: buys where price < 0.95
- [x] Group by slug (market window)
- [x] For each slug, separate UP and DOWN buys
- [x] Calculate arb pair: min(UP shares, DOWN shares)
- [x] Calculate combined cost per share, profit per share
- [x] Track standalone (unpaired) shares separately
- [x] Determine win/loss from redemption events

### Phase 3: Rendering

- [x] Arb pair rows: `💰 ARB 10+10 UP@0.49 DN@0.47 = 0.96` → `+$0.40`
- [x] Standalone rows: `✓ UP 5@0.49` → P&L
- [x] Date separators between days
- [x] Daily history cards with day-level aggregation
- [x] Top stats calculated from all data
- [x] Auto-refresh every 30 seconds

## Acceptance Criteria

- [x] Dashboard loads at `/arb-dashboard.html` on GitHub Pages
- [x] Correctly identifies arb pairs (equal UP+DOWN shares)
- [x] Splits unequal fills into arb portion + standalone portion
- [x] Shows 💰 for locked arb profits
- [x] Displays dollar P&L, ROI %, win rate
- [x] Uses same color scheme and design system as existing dashboard
- [x] Handles edge cases: no trades, single-side only, pending windows
