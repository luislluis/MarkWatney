---
title: "feat: OB Exit FOK Retry Loop with Hard Stop Escalation"
type: feat
date: 2026-02-06
---

# OB Exit FOK Retry Loop with Hard Stop Escalation

## Overview

Add a FOK retry loop to `execute_99c_early_exit()` so that when an OB exit triggers, shares are **guaranteed** to be sold. Currently a single FOK rejection returns False, and `ob_negative_ticks` may decay before the next tick retries — leaving the bot holding shares until the 60c hard stop (bigger loss).

## Problem Statement

**The gap in the current exit chain:**

1. OB exit fires (3 negative ticks), single FOK attempt rejects → returns `False`
2. Next tick, OB recovers above -0.30, `ob_negative_ticks` decays 3→2
3. OB exit doesn't trigger again until 3 consecutive negatives re-accumulate
4. Price keeps dropping → eventually hits hard stop at 60c → bigger loss

**Root cause:** Once the decision to exit is made, it must be committed to — not abandoned on first failure.

## Proposed Solution

Add a 5-attempt FOK retry loop inside `execute_99c_early_exit()`, following the proven pattern from `execute_hard_stop()` (lines 1016-1067). If all 5 retries fail, escalate to `execute_hard_stop()` which has its own 10-attempt retry loop.

**Escalation chain:** OB exit (5 FOK retries) → hard stop (10 FOK retries) → guaranteed exit

## File: `trading_bot_smart.py`

### Change 1: Add constant `OB_EXIT_FOK_MAX_RETRIES`

Near existing OB exit settings (lines 188-189), add:

```python
OB_EXIT_FOK_MAX_RETRIES = 5          # FOK retry attempts before escalating to hard stop
```

### Change 2: Rewrite `execute_99c_early_exit()` FOK section (lines 1298-1352)

Replace the current single-FOK-attempt block with a retry loop modeled on `execute_hard_stop()`. The pre-checks (hard stop fallback, shares validation, empty bids escalation, floor check) remain unchanged.

**New retry loop structure:**

```python
    # --- existing pre-checks above remain unchanged ---

    token = window_state.get(f'{side.lower()}_token')
    entry_price = window_state.get('capture_99c_fill_price', 0.99)

    print()
    print("🚨" * 20)
    print(f"🚨 99c OB EXIT TRIGGERED")
    print(f"🚨 Selling {shares:.0f} {side} shares (FOK market sell)")
    print(f"🚨 OB Reading: {trigger_value:+.2f}")
    print("🚨" * 20)

    # === FOK RETRY LOOP (v1.56) ===
    remaining_shares = shares
    total_pnl = 0.0
    best_bid = float(bids[0]['price'])  # From pre-check bids
    attempts = 0

    while remaining_shares > 0 and attempts < OB_EXIT_FOK_MAX_RETRIES:
        attempts += 1

        # Refresh books on retry (not first attempt)
        if attempts > 1:
            time.sleep(0.5)
            if market_data:
                refreshed = get_order_books(market_data)
                if refreshed:
                    books = refreshed
            # Re-read bids from refreshed books
            bids = books.get(f'{side.lower()}_bids', [])
            if not bids:
                print(f"[{ts()}] OB EXIT: No bids on attempt {attempts}, will retry")
                continue
            best_bid = float(bids[0]['price'])

        # Place FOK
        success, order_id, filled = place_fok_market_sell(token, remaining_shares)

        if success and filled > 0:
            fill_pnl = (best_bid - entry_price) * filled
            total_pnl += fill_pnl
            remaining_shares -= filled
            print(f"[{ts()}] OB EXIT: Filled {filled:.0f} @ ~{best_bid*100:.0f}c, P&L: ${fill_pnl:.2f}")
        elif success and filled == 0:
            # "not enough balance" → shares already sold
            print(f"[{ts()}] OB EXIT: Shares already sold (balance exhausted)")
            remaining_shares = 0
            break
        else:
            print(f"[{ts()}] OB EXIT: FOK rejected (attempt {attempts}/{OB_EXIT_FOK_MAX_RETRIES})")

    # === ESCALATION: If retry loop exhausted, escalate to hard stop ===
    if remaining_shares > 0:
        print(f"[{ts()}] OB EXIT: {attempts} FOK attempts failed, escalating to HARD STOP")
        # Update tracked shares before escalation so hard stop knows how many to sell
        window_state[f'capture_99c_filled_{side.lower()}'] = remaining_shares
        window_state[f'filled_{side.lower()}_shares'] = remaining_shares
        hs_success, hs_pnl = execute_hard_stop(side, books, market_data=market_data)
        total_pnl += hs_pnl
        # CRITICAL: Always mark exited after hard stop attempt (prevents cascading re-triggers)
        if not window_state.get('capture_99c_exited'):
            window_state['capture_99c_exited'] = True
            window_state['capture_99c_exit_reason'] = reason
        window_state['realized_pnl_usd'] = window_state.get('realized_pnl_usd', 0.0) + total_pnl
        return hs_success

    # === SUCCESS: All shares sold via FOK retry loop ===
    print(f"[{ts()}] OB EXIT: Sold {shares:.0f} @ ~{best_bid*100:.0f}c (entry {entry_price*100:.0f}c)")
    print(f"[{ts()}] OB EXIT: P&L = ${total_pnl:.2f}")

    log_event("99C_EARLY_EXIT", window_state.get('window_id', ''),
                    side=side, shares=shares, price=best_bid,
                    pnl=total_pnl, reason=reason, details=f"OB={trigger_value:.2f}")

    msg = f"""🚨 <b>99c EARLY EXIT</b>
Side: {side}
Shares: {shares:.0f}
Exit Price: ~{best_bid*100:.0f}c
Entry Price: {entry_price*100:.0f}c
OB Reading: {trigger_value:+.2f}
P&L: ${total_pnl:.2f}
<i>FOK market sell — guaranteed exit</i>"""
    send_telegram(msg)

    window_state['capture_99c_exited'] = True
    window_state['capture_99c_exit_reason'] = reason
    window_state[f'capture_99c_filled_{side.lower()}'] = 0
    window_state[f'filled_{side.lower()}_shares'] = 0
    window_state['realized_pnl_usd'] = window_state.get('realized_pnl_usd', 0.0) + total_pnl

    return True
```

### Change 3: Version bump → v1.56, update BOT_REGISTRY.md

### Change 4: Deploy, commit, push

## Critical Design Decisions (from SpecFlow analysis)

### 1. Always set `capture_99c_exited = True` after hard stop escalation

Even if hard stop fails. This prevents infinite re-entry into the retry loop every tick (the same pattern used at lines 2014-2016 and 2124-2126 in the main loop).

### 2. Do NOT update `window_state` share counts mid-loop

Only update after the loop completes (success) or before escalation to hard stop. If escalating, update tracked shares so hard stop knows the correct remaining count.

### 3. Floor price check stays as pre-loop gate

The 1-cent floor (`HARD_STOP_FLOOR = 0.01`) check at line 1294 stays before the loop. If bids are below 1 cent on entry, we abort (market is dead). If bids drop below 1 cent during the loop, the FOK will reject anyway, and after 5 failures the escalation to hard stop will sell at any price.

### 4. Remaining shares tracking (defensive)

FOK is atomic (all-or-nothing), so partial fills shouldn't happen. But we track `remaining_shares` defensively, matching `execute_hard_stop()`'s pattern, to handle unexpected API behavior.

### 5. Telegram notification: once only

- FOK retry loop succeeds → OB exit Telegram notification fires
- FOK retries fail, escalate to hard stop → hard stop sends its own Telegram notification
- No double notifications

### 6. Blocking duration acknowledged

- Best case: FOK fills on attempt 1, ~0s blocking
- Normal case: 2-3 retries, ~1-1.5s blocking
- Worst case (all retries + hard stop escalation + API timeouts): ~15-55s blocking
- This is acceptable — getting out of the position is more important than tick logging

## Acceptance Criteria

- [ ] OB exit retries FOK up to 5 times with 0.5s backoff + book refresh between attempts
- [ ] After 5 failed FOK attempts, escalates to `execute_hard_stop()`
- [ ] `capture_99c_exited` is set to True after hard stop escalation (even on failure)
- [ ] No duplicate Telegram notifications
- [ ] `remaining_shares` tracked defensively across retries
- [ ] `OB_EXIT_FOK_MAX_RETRIES = 5` defined as a configurable constant
- [ ] Version bumped to v1.56
- [ ] Deployed and running on server
- [ ] Syntax verified with `py_compile`

## References

- **Brainstorm:** `docs/brainstorms/2026-02-06-ob-exit-fok-retry-brainstorm.md`
- **Pattern source:** `execute_hard_stop()` retry loop at lines 1016-1067
- **Current function:** `execute_99c_early_exit()` at lines 1247-1352
- **FOK implementation:** `place_fok_market_sell()` at lines 874-929
- **Main loop caller:** line 2044
