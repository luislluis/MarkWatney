---
title: Supervisor Bot — Independent Trading Analyst
type: feat
date: 2026-03-03
---

# Supervisor Bot — Independent Trading Analyst

## What We're Building

A standalone Python supervisor bot (`supervisor_bot.py`) that runs as its own systemd service on the server, independently monitoring the arb trading bot. It does three things:

1. **Per-window audit** — For every 15-minute window, verify: Did both arb legs fill? Did the pair complete? What was the actual cost vs expected? Did profit-lock fire? What happened at settlement? Cross-checks the bot's log claims against Polymarket API ground truth.

2. **Problem diagnosis** — When something goes wrong (unpaired trade, bail, hard stop, rescue sell), figure out *why*. Was it thin order book depth? Price moved too fast? Second leg rejected? Provides a diagnosis and recommendation for each failure.

3. **Daily analysis** — Roll up the day's windows and identify patterns. What's causing wins? What's causing losses? E.g., "Both losses today were unpaired DOWN legs in windows with <$200 book depth. Recommendation: skip windows where DN book depth < $X."

Results are written to Supabase. A new **Watchdog tab** on `arb-dashboard.html` reads from Supabase and displays:
- **Scorecard summary** at top: pair rate, daily P&L verified, issue counts by type (# and % of windows)
- **Timeline** below: chronological feed of per-window audit results with diagnosis details

## Why This Approach

### Continuous service vs cron vs integrated

**Decision: Standalone systemd service (Approach 1)**

- **Independence is the whole point.** If the bot has a bug, the supervisor catches it precisely because it's a separate process with its own data pipeline. If the supervisor were integrated into the bot, a bot bug = supervisor bug.
- **Real-time detection.** A continuously running service can detect unpaired trades *during* the window, not 10 minutes later via cron. Speed matters for the #1 pain point (one leg fills, pair fails).
- **Matches existing pattern.** The trading bot already runs as `polybot.service`. The supervisor would be `polybot-supervisor.service` — same deployment model.

### Data sources: Bot log + Polymarket API

**Decision: Dual-source with clear roles**

- **Bot log (`~/polybot/bot.log`)** = the bot's perspective. What it *claims* happened. The supervisor tails this in real-time to track: orders placed, fills detected, state transitions (QUOTING -> PAIRING -> DONE), exits, errors, danger scores.
- **Polymarket API** = ground truth. The supervisor independently queries positions, activity, and order book to confirm: Are the positions the bot claims actually there? Did the fills actually happen at the prices claimed? Is the order book state consistent with what the bot logged?

The supervisor compares the two and flags discrepancies. This is the "triple confirmation" — bot says X, API says Y, supervisor judges.

### Dashboard: Watchdog tab on existing arb-dashboard.html

**Decision: New tab on existing dashboard, not a separate page**

- Keeps everything in one place — you open the arb dashboard and switch to Watchdog when you want the supervisor's view.
- Scorecard at top mirrors the existing summary cards pattern (P&L, win rate, etc.) but adds issue-type breakdown.
- Timeline below mirrors the existing trade list pattern but shows audit results instead of trades.

## Key Decisions

1. **Standalone systemd service** — `supervisor_bot.py` runs as `polybot-supervisor.service`, independent from the trading bot
2. **Dual data source** — Bot log for bot's perspective, Polymarket API for ground truth
3. **Writes to Supabase** — New table(s) for audit results, the dashboard reads from Supabase
4. **Watchdog tab on arb-dashboard.html** — Not a separate page, a new tab on the existing dashboard
5. **Scorecard + timeline layout** — Summary stats at top (pair rate, P&L, issue counts by type as # and %), per-window audit cards below
6. **Issue taxonomy** — Track distinct issue types (unpaired, bail, hard stop, rescue sell, discrepancy) with counts and percentages
7. **Diagnosis + recommendations** — Each failed window gets a root cause analysis and actionable suggestion
8. **Daily pattern analysis** — End-of-day (or rolling) analysis of what's driving wins vs losses
9. **No auto-intervention** — Supervisor observes and reports only, never places orders

## Issue Types to Track

| Issue | Description | Severity |
|-------|-------------|----------|
| UNPAIRED | One leg filled, pair failed — stranded position | Critical |
| BAIL | Bot had to bail/rescue an imbalanced position | High |
| HARD_STOP | Emergency exit triggered (bid collapsed) | High |
| PROFIT_LOCK_MISS | 99c fill but profit lock sell didn't fill | Medium |
| PRICE_DISCREPANCY | Bot-reported fill price differs from API | Medium |
| POSITION_MISMATCH | Bot's position count doesn't match API | Medium |
| CLEAN_PAIR | Both legs filled, settled correctly | OK (tracked for ratios) |

## Open Questions

1. **Supabase table schema** — Exact columns for audit results (to be decided in planning)
2. **How far back to analyze** — Does the supervisor only look at today, or maintain a rolling history?
3. **Telegram alerts from supervisor?** — Should the supervisor also send Telegram for critical issues (UNPAIRED), or dashboard-only?
4. **Recommendation engine depth** — How smart should the daily analysis be? Simple pattern matching ("losses correlate with thin books") or something more sophisticated?
