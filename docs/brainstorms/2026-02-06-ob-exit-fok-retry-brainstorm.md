# Brainstorm: Resilient OB Exit with FOK Retry

**Date:** 2026-02-06
**Status:** Ready for implementation

## What We're Building

Add a FOK retry loop to `execute_99c_early_exit()` so that when an OB exit triggers, shares are **guaranteed** to be sold. Currently a single FOK rejection returns False, relying on the next tick to retry — but `ob_negative_ticks` may decay, leaving the bot holding shares until the 60c hard stop (bigger loss).

## Why This Matters

**The gap:**
1. OB exit fires (3 negative ticks), FOK rejects → returns False
2. Next tick, OB recovers above -0.30, `ob_negative_ticks` decays 3→2
3. OB exit doesn't trigger again until 3 consecutive negatives re-accumulate
4. Price keeps dropping → eventually hits hard stop at 60c → bigger loss

**The fix:** Once OB exit decides to sell, commit to selling — retry FOK up to 5 times, then escalate to hard stop.

## Key Decisions

1. **Retry inside the function** (block for ~2.5s max) rather than flag-based tick retries
2. **5 FOK retry attempts** with ~0.5s between each, refreshing books each time
3. **Escalate to execute_hard_stop()** after 5 failed attempts (hard stop has its own 10-attempt retry loop)
4. **Escalation chain:** OB exit (5 retries) → hard_stop (10 retries) → guaranteed exit

## Implementation Notes

- Follow `execute_hard_stop`'s retry pattern: check bids, refresh books, 0.5s backoff
- Track `remaining_shares` across retries (FOK can partially fill if matched amount < requested)
- Handle "not enough balance" (success=True, filled=0) as completion
- On escalation, pass `market_data` through to hard stop for book refresh capability
- Log each retry attempt for debugging

## Open Questions

None — ready for `/workflows:plan`.
