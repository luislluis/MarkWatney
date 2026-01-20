# Polymarket Bot: Performance Tracker

## What This Is

A real-time performance tracking bot that runs alongside the Polymarket trading bot. Monitors BTC 15-minute window trades, grades each window's ARB and 99c capture performance, and writes formatted results to a dedicated Google Sheet dashboard.

**Current Version:** v2.0 "Performance Tracker"

## Core Value

**See trading performance at a glance with real-time grading of every window.**

Quick visual feedback on what's working and what's not. Green means money, red means learn.

## Current Milestone: v2.0 Performance Tracker

**Goal:** Build a standalone dashboard bot that grades every trading window in real-time.

**Target features:**
- Independent bot running alongside trading bot
- Own Google Sheet with per-window grading
- ARB trade tracking (entry, result, P/L)
- 99c capture tracking (entry, result, P/L)
- Color-coded formatting (green wins, red losses)
- Summary row with totals and win rates

## Requirements

### Validated

These capabilities exist in the trading bot (reference only):

- ✓ BTC 15-minute Up/Down arbitrage trading
- ✓ 99c capture strategy
- ✓ Multi-signal danger scoring system — v1.0
- ✓ OB-based early bail detection — v1.9
- ✓ 5-second rule for ARB pairing — v1.10

### Active

- [ ] Standalone tracker bot (separate from trading bot)
- [ ] Position monitoring via Polymarket APIs
- [ ] Window-by-window grading after each 15-min close
- [ ] ARB trade detection and grading
- [ ] 99c capture detection and grading
- [ ] Own Google Sheet with formatted dashboard
- [ ] Color coding (green/red) and emoji indicators
- [ ] Summary row with totals and win rates
- [ ] Real-time updates as windows close

### Out of Scope

- Modifying the trading bot — this is a separate observer
- Historical backfill — starts fresh, grades going forward
- Alerts/notifications — just logging for now
- Trade recommendations — pure observation and grading

## Constraints

- **Independence**: Must not interfere with trading bot operation
- **Same server**: Runs on 174.138.5.183 alongside trading bot
- **API limits**: Use same APIs but don't duplicate calls unnecessarily
- **Google Sheets**: Use existing service account credentials

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Separate bot | Clean separation, no risk to trading | — Pending |
| Own Google Sheet | Fresh dashboard, not cluttering trading logs | — Pending |
| Watch positions | Most reliable way to detect actual trades | — Pending |

---
*Last updated: 2026-01-20 after v2.0 milestone start*
