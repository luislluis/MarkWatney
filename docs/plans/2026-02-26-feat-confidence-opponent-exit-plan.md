---
title: "Confidence + Opponent Ask Gated Exit"
type: feat
date: 2026-02-26
---

# v1.56: Confidence + Opponent Ask Gated Exit

## Overview

Add a new exit trigger that fires when the danger score (already calculated every tick) exceeds 0.40 AND the opponent's ask price is above 15c. This catches real reversals early (while bids are still at 70-80c) while avoiding false exits on winning trades where the opponent ask is always ≤8c.

## Problem Statement

On 2026-02-25, a hard stop triggered on 281 UP shares. By the time bids collapsed to 40c (the hard stop trigger), actual FOK fills were at ~26c — a $382.69 loss. The 40c hard stop is a **lagging indicator**: by the time bids reach 40c, liquidity is already dead.

Additionally, the 40c hard stop **falsely triggered today** on the 04:44 window — selling a winning trade at ~35c.

**Key insight from today's data (13 winning windows + 1 loss):**
- On ALL 13 wins: opponent ask was ≤8c (market had no doubt)
- On the 1 loss: opponent ask was 65c (real market uncertainty)
- The danger score infrastructure is 100% built and calculated every tick — just not wired to any exit

## Proposed Solution

Wire the existing `danger_score` to trigger `execute_hard_stop()` when gated by opponent ask:

```
EXIT if danger_score >= 0.40
  AND opponent_ask > 0.15
  AND remaining_secs > 15
```

Requires 2 consecutive ticks (same as current hard stop) to prevent single-tick noise.

Keep existing 40c hard stop as absolute backstop — unchanged.

## Backtested Results (2026-02-25/26)

| Window | Result | Opponent Ask Max | Would Exit? |
|--------|--------|-----------------|-------------|
| 13 winning windows | WIN | ≤8c | NO (correct) |
| 1 losing window | LOSS at 26c | 65c | YES — would have exited at 70-80c bids |

**False positive rate: 0%** on today's data.

## Technical Approach

### Change 1: Add constants (after line 386)

```python
# ===========================================
# DANGER EXIT — Confidence + Opponent Ask Gate (v1.56)
# ===========================================
DANGER_EXIT_ENABLED = True
DANGER_EXIT_THRESHOLD = 0.40          # Same as existing DANGER_THRESHOLD
DANGER_EXIT_OPPONENT_ASK_MIN = 0.15   # Only exit if opponent ask > 15c (real uncertainty)
DANGER_EXIT_CONSECUTIVE_REQUIRED = 2  # Require 2 consecutive ticks (same as hard stop)
```

### Change 2: Add danger exit check to main loop (after line 3974, before state machine)

Wire into the existing danger score calculation block. The `danger_result` and `opponent_ask` are already computed at lines 3960-3971. Add the gated exit check immediately after:

```python
# === DANGER EXIT — Confidence + Opponent Ask Gate (v1.56) ===
if (DANGER_EXIT_ENABLED and
    not window_state.get('capture_99c_exited') and
    not window_state.get('capture_99c_hedged') and
    remaining_secs > 15):

    danger_score = danger_result['score']
    opp_ask = danger_result['opponent_ask']

    if danger_score >= DANGER_EXIT_THRESHOLD and opp_ask > DANGER_EXIT_OPPONENT_ASK_MIN:
        window_state['danger_exit_ticks'] = window_state.get('danger_exit_ticks', 0) + 1
        ticks = window_state['danger_exit_ticks']
        if ticks >= DANGER_EXIT_CONSECUTIVE_REQUIRED:
            print(f"[{ts()}] 🚨 DANGER_EXIT: score={danger_score:.2f} opp_ask={opp_ask*100:.0f}c ({ticks} ticks)")
            log_activity("DANGER_EXIT", {
                "danger_score": danger_score,
                "opponent_ask": opp_ask,
                "ticks": ticks,
                "components": danger_result
            })
            execute_hard_stop(capture_side, books)
        else:
            print(f"[{ts()}] ⚠️ DANGER_WARNING: tick {ticks}/{DANGER_EXIT_CONSECUTIVE_REQUIRED}: score={danger_score:.2f} opp_ask={opp_ask*100:.0f}c")
    else:
        if window_state.get('danger_exit_ticks', 0) > 0:
            print(f"[{ts()}] DANGER_EXIT: Reset (score={danger_score:.2f} opp_ask={opp_ask*100:.0f}c)")
        window_state['danger_exit_ticks'] = 0
```

**Key:** This block goes INSIDE the existing `if window_state.get('capture_99c_fill_notified')` block (line 3933), right after `window_state['danger_result'] = danger_result` (line 3971) and before the hedge check (line 3974). The `capture_side`, `opponent_ask`, and `danger_result` variables are already in scope.

### Change 3: Add `danger_exit_ticks` to window_state init (line ~503)

```python
"danger_exit_ticks": 0,              # Danger exit: consecutive ticks above threshold
```

### Change 4: Version bump

```python
BOT_VERSION = {
    "version": "v1.56",
    "codename": "Danger Sense",
    "date": "2026-02-26",
    "changes": "Danger score exit gated by opponent ask > 15c; 2-tick consecutive requirement"
}
```

### Change 5: Update CLAUDE.md

Add to "Verified Bot Rules & Settings" table:

| Setting | Value | Why |
|---------|-------|-----|
| `DANGER_EXIT_ENABLED` | `True` | Exits when danger_score >= 0.40 AND opponent_ask > 15c. Catches reversals before bid collapse. |
| `DANGER_EXIT_THRESHOLD` | `0.40` | Same threshold that was already defined but unused. |
| `DANGER_EXIT_OPPONENT_ASK_MIN` | `0.15` (15c) | The key gate: winning trades NEVER have opponent ask > 8c. 15c provides margin. |
| `DANGER_EXIT_CONSECUTIVE_REQUIRED` | `2` ticks | Prevents single-tick noise. Same pattern as hard stop. |

Add to "Common Mistakes to Avoid":
- **Don't remove the opponent_ask gate from danger exit** — danger score alone causes false exits (winning trades score 3.01-3.16 vs loss at 2.09 due to end-of-window settlement). The opponent_ask > 15c gate is what distinguishes real reversals from normal settlement.

### Change 6: Update BOT_REGISTRY.md

Add v1.56 entry with codename "Danger Sense".

## Acceptance Criteria

- [ ] `DANGER_EXIT_ENABLED = True` with threshold 0.40 and opponent ask gate at 15c
- [ ] Exit requires 2 consecutive ticks (same pattern as hard stop)
- [ ] Exit calls `execute_hard_stop()` (reuses chunked FOK infrastructure from v1.55)
- [ ] `DANGER_WARNING` log messages appear on first tick, `DANGER_EXIT` on second
- [ ] Counter resets when either danger score drops below 0.40 OR opponent ask drops below 15c
- [ ] Existing 40c hard stop remains unchanged as backstop
- [ ] No changes to entry logic, confidence calculation, or danger score formula
- [ ] Version bumped to v1.56 in bot and BOT_REGISTRY.md
- [ ] CLAUDE.md updated with new settings and lesson learned

## Files Modified

| File | Changes |
|------|---------|
| `trading_bot_smart.py` | 4 constants, gated exit check in main loop, window_state init, version bump |
| `CLAUDE.md` | New settings, lesson learned |
| `BOT_REGISTRY.md` | v1.56 entry |

## Why This Is Safe

1. **Zero false positives on today's data** — opponent ask never exceeded 8c on any winning trade
2. **15c threshold provides 7c margin** above the observed 8c maximum on wins
3. **2-tick consecutive requirement** filters momentary noise
4. **40c hard stop unchanged** as absolute backstop
5. **No changes to entry logic** — only affects exit behavior after fill
6. **Reuses existing infrastructure** — danger score calculated every tick, `execute_hard_stop()` already handles chunked FOK
