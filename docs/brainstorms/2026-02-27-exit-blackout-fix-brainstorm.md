---
title: Fix Exit Blackout — Profit Lock Blocks Main Loop + Exit Triggers Too Late
type: fix
date: 2026-02-27
tags: [exit-strategy, profit-lock, hard-stop, danger-exit, safety-exit]
---

# Fix Exit Blackout: Profit Lock Blocks Main Loop + Exit Triggers Fire Too Late

## The Problem

On 2026-02-27, the bot suffered two losses totaling -$39.25 on 99c capture trades:

| Window (ET) | Entry | Exit | Fill Price | Loss | Time to Exit |
|-------------|-------|------|-----------|------|-------------|
| 8:15-8:30 PM | 25 UP @ 95c | Hard stop | 23c | -$18.00 | 22 seconds |
| 8:45-9:00 PM | 25 UP @ 95c | Hard stop | 10c | -$21.25 | 56 seconds |

Both times, UP was at 95c+ (winner), BTC reversed, UP crashed to 10-23c, and the hard stop filled far below the 45c trigger.

## Root Cause Analysis

### Bug 1: Profit Lock Blocks Main Loop for 6 Seconds (PRIMARY)

The profit lock "fast path" runs 10 synchronous retries in the main thread, each checking share balance with ~0.5s delay:

```
20:29:25  UP:98c  ← capture fills
20:29:26  PROFIT_LOCK_RETRY: Attempt 1 | Balance: 0.0
  ...6 seconds of retries, ZERO monitoring...
20:29:32  "Fast path failed. Launching background thread."
20:29:32  UP:50c  ← main loop resumes, crash already happened
20:29:35  UP:6c   ← hard stop fires, fills at garbage price
```

During those 6 seconds: no status lines, no exit checks, no price monitoring. The crash from 98c to 50c happens completely unseen.

### Bug 2: Zombie Profit Lock Threads

Background profit lock retry threads from PREVIOUS windows never stop. Log shows "Attempt 353, 354, 355, 356" — a thread that's been retrying for hours, wasting resources and adding noise.

### Bug 3: Position State Not Set Before Profit Lock

After capture fill, the main loop shows `pos:0/0` even after 25 shares are filled. Exit triggers that gate on `capture_99c_fill_notified` don't fire because the fill state isn't set until after the profit lock attempt.

### Bug 4: Hard Stop Triggers at 45c but Fills at 10-23c

In binary markets, when BTC reverses with <2 min left, bids collapse from 95c to sub-20c in seconds. The 45c trigger fires, but by the time the FOK executes (1-3 seconds), bids are at 10-23c.

### Bug 5: Danger Exit is Disabled

The danger exit (`opponent_ask > 15c + danger_score >= 0.40`) was designed exactly for this scenario — catching reversals while bids are still at 70-80c. But `DANGER_EXIT_ENABLED = False`. On winning trades, opponent ask stays < 8c (zero false positives in backtesting).

### Bug 6: execute_ob_exit() Too Slow for End-of-Window

SAFETY_EXIT detected bid at 73c with T-1s remaining, but called `execute_ob_exit()` which does:
1. API position verification (~1-2s)
2. Fresh order book fetch (~1s)
3. Multi-pass bid walking
4. Retry loops with `time.sleep(5)` and `time.sleep(10)`

By the time orders hit the exchange, the window had ended. Sell filled at 10c (5 seconds after window close).

## What We're Building

A comprehensive fix addressing all six bugs:

### Fix 1: Non-Blocking Profit Lock (Highest Priority)

Move ALL profit lock retries to a background thread from the start. The main loop must NEVER block on profit lock.

Sequence after capture fill:
1. Set `capture_99c_fill_notified` and position tracking IMMEDIATELY (before profit lock)
2. Launch profit lock in background thread
3. Main loop continues monitoring every tick — exit triggers armed instantly

### Fix 2: Kill Zombie Threads

Profit lock background thread checks `window_state['window_id']` on each retry. If the window has changed, stop immediately. No more "Attempt 353" from a stale window.

### Fix 3: Three-Tier Exit Ladder

| Tier | Trigger | Expected Fill | Purpose |
|------|---------|---------------|---------|
| 1. Danger Exit | opponent_ask > 15c + danger > 0.40 | 80-90c | Early warning, catches reversals |
| 2. Hard Stop | bid <= 65c (2 consecutive ticks) | 55-65c | Backstop before full collapse |
| 3. Safety Exit | bid < 80c + T-10s | 60-75c | Final seconds protection |

- **Re-enable** `DANGER_EXIT_ENABLED = True`
- **Raise** `HARD_STOP_TRIGGER` from 0.45 to 0.65
- **Keep** `WINDOW_END_SAFETY_PRICE = 0.80` (already live)

### Fix 4: Fast-Path FOK for Final 30 Seconds

When `remaining_secs <= 30`, ALL exit triggers bypass `execute_ob_exit()` and fire a single immediate FOK:
- No API position verification (use `window_state` tracked shares)
- No order book walking
- No retries
- One FOK at `HARD_STOP_FLOOR` (1c), takes <1 second

## Why This Approach

- **Non-blocking profit lock** eliminates the 6-second blind spot — this alone would have prevented both losses
- **Danger exit** catches reversals at 80-90c while bids are thick (proven zero false positives on winners)
- **65c hard stop** provides a higher backstop when danger exit misses
- **Fast-path FOK** ensures any end-of-window exit completes before the order book disappears

## Key Decisions

1. **Profit lock must be 100% async** — zero main loop blocking, ever
2. **Set fill state BEFORE profit lock** — exit triggers must be armed instantly on fill
3. **Re-enable danger exit** — the data supports it (opponent ask < 8c on all wins, > 15c on all losses)
4. **Hard stop at 65c** — compromise between catching crashes early (was 45c → fills at 10-23c) and avoiding false exits (winners stay 95c+). May need validation against tick data.
5. **Fast-path FOK in final 30s** — speed > precision when the window is about to end

## Open Questions

1. **65c hard stop threshold** — Should we pull tick data from Google Sheets to verify winners never dip below 65c? Or is 65c conservative enough given winners stay 95c+?
2. **Danger exit thresholds** — Keep current 0.40/15c or adjust? The backtested values worked on 2026-02-25/26 data.
3. **Profit lock cancel threshold** — Currently cancels sell if bid < 60c. With hard stop raised to 65c, should this change to match?
4. **Two bot instances** — The server was running two bot instances simultaneously (duplicate log lines). Need to ensure only one instance runs.

## Evidence

### Polymarket Activity API (Feb 27 evening)
```
8:28:11 PM | BUY  | 25 UP @ 95c ($23.75) | slug: ...1772241300
8:28:33 PM | SELL | 25 UP @ 23c ($5.71)  | hard stop filled at 23c
8:43:49 PM | BUY  | 25 DN @ 95c ($23.75) | slug: ...1772242200
8:44:35 PM | SELL | 25 DN @ 99c ($24.75) | profit lock worked perfectly
8:59:09 PM | BUY  | 25 UP @ 95c ($23.75) | slug: ...1772243100
9:00:05 PM | SELL | 25 UP @ 10c ($2.50)  | hard stop filled at 10c (after window end!)
```

### Server Log Evidence
```
SAFETY_EXIT: T-1s, UP best bid 73c < 80c — exiting to protect position
HARD_STOP_ERROR: No orderbook exists for the requested token id (x15 attempts)
HARD_STOP_ERROR: Failed to fully liquidate! 25 shares stuck
PROFIT_LOCK_RETRY: Attempt 353 | Balance: 0.0  ← zombie thread from hours ago
```
