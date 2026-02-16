# Paper Trading After ROI Target

**Date:** 2026-02-16
**Status:** Ready for planning

## What We're Building

When the bot hits ~45% ROI, it stops placing real orders but continues running the exact same trading logic in "paper mode." Every decision the bot would have made — entries, fills, exits, PnL — gets simulated and logged, so we can track how the strategy performs beyond the ROI cutoff without risking gains.

## Why This Approach

**Paper Mode Gate** — a single `paper_mode` flag that gates all order execution inside the existing bot. The bot runs identically (same logic, same order book fetches, same PnL calcs) but skips CLOB API calls and simulates fills instead.

Chosen over:
- **Shadow Bot Process** — overkill, duplicates code and deployment complexity
- **Post-Hoc Simulation** — delayed, loses order book depth, doesn't match the "keep it running" intent

Benefits:
- Minimal code changes (one flag, a few conditionals)
- Paper data is directly comparable to real data (same decision logic)
- Zero risk of accidental real orders

## Key Decisions

1. **Full simulation** — track entries, fills, PnL per window, and cumulative paper PnL (not just ticks)
2. **Same tables, PAPER tag** — log paper trades to the same Supabase/Sheets tables with a `paper` flag/column to distinguish them
3. **Full stop on real activity** — no redemptions or real orders while in paper mode
4. **Realistic fill simulation** — check if our bid price appears in the order book (ask <= bid) rather than assuming instant fills
5. **Persistent paper mode** — save state to a file so the bot resumes in paper mode after restart
6. **ROI trigger ~45%** — when cumulative session ROI hits the target, flip to paper mode for the remainder of the session

## Open Questions

1. **ROI calculation basis** — is 45% ROI based on total capital deployed, or on some fixed bankroll number? Need to clarify the denominator.
2. **Can the user manually exit paper mode?** — e.g., a flag in the state file or an environment variable to force back to real trading.
3. **Paper mode dashboard** — should the Sheets/Supabase dashboard visually separate paper vs real results, or just filter by the tag?
