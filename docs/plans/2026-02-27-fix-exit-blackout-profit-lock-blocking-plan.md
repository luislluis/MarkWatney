---
title: "fix: Exit Blackout — Non-Blocking Profit Lock + Three-Tier Exit Ladder + Fast-Path FOK"
type: fix
date: 2026-02-27
version: v1.61
codename: TBD
brainstorm: docs/brainstorms/2026-02-27-exit-blackout-fix-brainstorm.md
---

# fix: Exit Blackout — Non-Blocking Profit Lock + Three-Tier Exit Ladder + Fast-Path FOK

## Overview

The bot loses money because its profit lock retry loop blocks the main thread for 6 seconds after every capture fill. During those 6 seconds, no exit triggers run, and price crashes from 98c to 50c go completely undetected. Two losses totaling -$39.25 on 2026-02-27 were caused by this exact sequence. Additionally, the hard stop at 45c fires too late (fills at 10-23c), the danger exit is disabled, and end-of-window exits use a slow multi-pass function that can't complete before the window ends.

## Problem Statement

Six bugs compound to create catastrophic exit failure:

1. **Profit lock fast path blocks main loop 6 seconds** (lines 4473-4482) — 10 synchronous retries with `time.sleep(0.5)` freeze all monitoring
2. **Zombie profit lock threads** (lines 4497-4526) — background retry never checks if window changed; "Attempt 353" from stale windows
3. **Fill state set before profit lock but profit lock runs before main loop resumes** — exit triggers are armed (`fill_notified=True` at line 4426) but can't fire during the blocking loop
4. **Hard stop at 45c fills at 10-23c** (line 333) — too late in binary market crashes
5. **Danger exit disabled** (line 396) — the one exit designed for this exact scenario is turned off
6. **execute_ob_exit() too slow for end-of-window** (line 4727) — multi-pass bid walk with API calls takes 5+ seconds; at T-1s, order book disappears

## Proposed Solution

### Phase 1: Non-Blocking Profit Lock (Critical — fixes the 6s blackout)

**`trading_bot_smart.py` lines 4440-4526**

Replace the synchronous fast path + deferred background thread with a single immediate background thread:

```python
# BEFORE (lines 4473-4526): 10 synchronous retries blocking main loop
for pl_attempt in range(10):
    pl_success, pl_result = _try_profit_lock_sell(...)
    if pl_success:
        break
    time.sleep(0.5)  # ← BLOCKS MAIN LOOP

# AFTER: Immediately launch background thread, main loop continues
import threading
_window_id = window_state.get('window_id', '')
threading.Thread(
    target=_profit_lock_retry_loop,
    args=(sell_token, sell_price, sell_shares, side, _ws_ref, _window_id),
    daemon=True
).start()
```

Changes:
- [ ] **Remove synchronous fast path** (delete lines 4473-4482 loop)
- [ ] **Launch background thread immediately** after fill detection (line ~4444)
- [ ] **Add window_id parameter** to `_profit_lock_retry_loop` — thread stops if `window_state['window_id'] != _window_id` (kills zombies)
- [ ] **Keep retry interval at 0.5s** in background thread (same speed, just non-blocking)
- [ ] **Cap max retries at 60** (30 seconds) — after that, give up and let exit triggers handle it

### Phase 2: Re-enable Danger Exit + Raise Hard Stop (fixes detection timing)

**Constants to change:**

```python
# trading_bot_smart.py line 396
DANGER_EXIT_ENABLED = True  # was False

# trading_bot_smart.py line 333
HARD_STOP_TRIGGER = 0.65    # was 0.45
```

Three-tier exit ladder result:

| Tier | Trigger | Line | Expected Fill |
|------|---------|------|---------------|
| Danger Exit | opponent_ask > 15c + danger > 0.40 (2 ticks) | 4674 | 80-90c |
| Hard Stop | bid <= 65c (2 consecutive ticks) | 4531 | 55-65c |
| Safety Exit | bid < 80c + T-10s | 4709 | 60-75c |

Changes:
- [ ] **Set `DANGER_EXIT_ENABLED = True`** (line 396)
- [ ] **Set `HARD_STOP_TRIGGER = 0.65`** (line 333)
- [ ] **Update comment** on line 332 from "45¢ hard stop" to "65¢ hard stop"
- [ ] **Update comment** on line 327 from "60¢ HARD STOP" to "65¢ HARD STOP"
- [ ] **Update `PROFIT_LOCK_CANCEL_THRESHOLD`** from 0.60 to 0.70 (line 408) — cancel sell before hard stop triggers, not after

### Phase 3: Fast-Path FOK for Final 30 Seconds (fixes end-of-window execution speed)

When any exit trigger fires with `remaining_secs <= 30`, bypass `execute_ob_exit()` and call `execute_hard_stop()` directly.

**Affects three locations:**

```python
# Hard stop (line 4546): currently calls execute_ob_exit
# Change to:
if remaining_secs <= 30:
    execute_hard_stop(capture_side, books)
else:
    ob_success, ob_pnl, ob_fills = execute_ob_exit(capture_side, books)
    if not ob_success:
        execute_hard_stop(capture_side, books)

# Danger exit (line 4693): same pattern
# Safety exit (line 4727): same pattern
```

Changes:
- [ ] **Hard stop exit** (line 4546): Add `remaining_secs <= 30` fast-path to `execute_hard_stop` directly
- [ ] **Danger exit** (line 4693): Add `remaining_secs <= 30` fast-path
- [ ] **Safety exit** (line 4727): Add `remaining_secs <= 30` fast-path

### Phase 4: Version Bump + Registry

- [ ] **Version bump** to v1.61 (line 23-28)
- [ ] **Update `BOT_REGISTRY.md`** with changes

## Acceptance Criteria

- [ ] Profit lock retry loop runs in background thread from the start — zero `time.sleep` in main loop after capture fill
- [ ] Status lines continue printing every second immediately after fill (no 6s gap)
- [ ] Profit lock background thread stops when window changes (no zombie threads)
- [ ] Background thread caps at 60 retries (30 seconds max)
- [ ] `DANGER_EXIT_ENABLED = True` — fires when opponent_ask > 15c + danger > 0.40
- [ ] `HARD_STOP_TRIGGER = 0.65` — triggers 20c earlier than before
- [ ] `PROFIT_LOCK_CANCEL_THRESHOLD = 0.70` — cancels before hard stop zone
- [ ] All three exit triggers use fast-path FOK when `remaining_secs <= 30`
- [ ] FINAL_SECONDS logging still works (already in main loop, unaffected)
- [ ] No `remaining_secs > 15` gates remain on exit triggers (already removed in v1.60)

## Implementation Order

| Step | What | Why First |
|------|------|-----------|
| 1 | Non-blocking profit lock | Fixes the 6s blindspot — the #1 cause of losses |
| 2 | Kill zombie threads | Prevents "Attempt 353" noise and resource waste |
| 3 | Enable danger exit | Adds early detection at 80-90c bids |
| 4 | Raise hard stop to 65c | Better backstop when danger exit misses |
| 5 | Update profit lock cancel to 70c | Cancel sell before hard stop zone |
| 6 | Fast-path FOK at T-30s | Ensures end-of-window exits complete in time |
| 7 | Version bump + registry | Track the release |

## Line-Level Change Summary

| File | Line(s) | Change |
|------|---------|--------|
| `trading_bot_smart.py` | 23-28 | Version bump to v1.61 |
| `trading_bot_smart.py` | 327 | Comment: "65¢ HARD STOP" |
| `trading_bot_smart.py` | 332 | Comment: "Enable 65¢ hard stop" |
| `trading_bot_smart.py` | 333 | `HARD_STOP_TRIGGER = 0.65` |
| `trading_bot_smart.py` | 396 | `DANGER_EXIT_ENABLED = True` |
| `trading_bot_smart.py` | 408 | `PROFIT_LOCK_CANCEL_THRESHOLD = 0.70` |
| `trading_bot_smart.py` | 4473-4526 | Replace sync fast path with immediate background thread + window_id check + max retries |
| `trading_bot_smart.py` | 4546 | Fast-path FOK when remaining_secs <= 30 |
| `trading_bot_smart.py` | 4693 | Fast-path FOK when remaining_secs <= 30 |
| `trading_bot_smart.py` | 4727 | Fast-path FOK when remaining_secs <= 30 |
| `BOT_REGISTRY.md` | append | v1.61 entry |

## Risk Analysis

| Risk | Mitigation |
|------|-----------|
| 65c hard stop causes false exits on winners | Winners stay 95c+ — 30c margin. Danger exit (opponent_ask gate) provides first line with proven zero false positives. |
| Background profit lock thread misses sell | Thread still runs at 0.5s intervals, same timing. Profit lock monitor (line 4557) checks fill status every tick. |
| Danger exit false positives | Requires opponent_ask > 15c (winners never > 8c) AND danger > 0.40 AND 2 consecutive ticks. Backtested on 2026-02-25/26 with zero false exits on 13 winning windows. |
| Fast-path FOK fills at bad price | Better than execute_ob_exit taking 5+ seconds and filling after window ends. At T-30s with 65c trigger, bids are still 50-60c — much better than the 10c fills we're getting now. |

## Verification

1. Deploy to server, watch for capture fill — status lines should continue immediately (no 6s gap)
2. Check logs for `PROFIT_LOCK_RETRY` — should appear with `[BG]` prefix from background thread, never blocking status lines
3. Verify no "Attempt 300+" zombie threads after window changes
4. Watch for `DANGER_WARNING` / `DANGER_EXIT` messages confirming danger exit is active
5. Confirm hard stop comment and trigger value show 65c in startup log
6. Test end-of-window exit: if hard stop fires with T < 30s, should see direct `execute_hard_stop` call (no `OB_EXIT: Starting PASS 1`)

## References

- Brainstorm: `docs/brainstorms/2026-02-27-exit-blackout-fix-brainstorm.md`
- Previous exit strategy plan: `docs/plans/2026-02-26-feat-confidence-opponent-exit-plan.md`
- Previous profit lock plan: `docs/plans/2026-02-27-feat-instant-profit-lock-plan.md`
- CLAUDE.md "Verified Bot Rules" section: Hard Stop, Danger Exit, Profit Lock subsections
